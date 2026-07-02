"""Equivalente CLI de `/analiza <PAR> [operar]` — sección 21.2.

Uso:
    python -m scripts.analiza SOLUSDT              # modo informe (nunca ejecuta)
    python -m scripts.analiza SOLUSDT operar        # modo operar (respeta risk engine)

Ambos modos corren el pipeline completo (scanner + técnico + risk engine).
El modo informe SIEMPRE registra `final_action=watchlist` y nunca ejecuta.
El modo operar únicamente registra `final_action=enter` si el risk engine
aprueba Y el executor está implementado — en este build (fase 1 en curso,
sin `binance_executor.py` todavía) nunca se envía ninguna orden real; se
dice explícitamente en el informe.
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.config import Settings, get_settings
from core.enums import FinalAction, RejectionReason, Trigger
from core.git_info import get_git_sha
from core.schemas.decision import DecisionRecord
from db.models import DecisionLog, RegimeLog
from db.session import get_session
from journal.decision_logger import log_decision
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import upsert_asset, upsert_candles
from services.risk.engine import RiskInput, evaluate_risk
from services.risk.portfolio_state import build_portfolio_snapshot
from services.scanner.filters import run_hard_filters
from services.scanner.regime import blocks_new_entries, compute_btc_regime
from services.technical.indicators import candles_to_frame
from services.technical.setups import detect_range_breakout
from services.technical.signal_builder import build_technical_signal

STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP"}
BTC_ASSET = "BTCUSDT"


async def _check_manual_rate_limit(session, settings: Settings, now: datetime) -> bool:
    stmt = select(func.count(DecisionLog.id)).where(
        DecisionLog.trigger == Trigger.manual.value,
        DecisionLog.ts >= now - timedelta(hours=1),
    )
    result = await session.execute(stmt)
    count = result.scalar_one()
    return count < settings.manual_max_per_hour


def _jsonable_breakout(detection: dict | None) -> dict | None:
    if detection is None:
        return None
    return {**detection, "open_time": str(detection["open_time"]), "close_time": str(detection["close_time"])}


def _print_header(asset: str, trigger_mode: str) -> None:
    print("=" * 60)
    print(f"  /analiza {asset}  ({trigger_mode})")
    print("=" * 60)


async def analiza(asset: str, operar: bool) -> None:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    asset = asset.upper().strip()
    trigger_mode = "operar" if operar else "informe"
    _print_header(asset, trigger_mode)

    if not asset.endswith("USDT"):
        print("Rechazado: en el MVP solo se admiten pares cotizados en USDT.")
        return

    base = asset.removesuffix("USDT")
    if base in STABLECOIN_BASES or asset in STABLECOIN_BASES:
        print(
            "Las stablecoins no son activos operables: sin volatilidad "
            "direccional no hay setup posible (sección 21.5). No se "
            "realiza análisis."
        )
        return

    client = BinanceMarketData()

    exists = await asyncio.to_thread(client.symbol_exists, asset)
    if not exists:
        print(f"Rechazado: {asset} no existe en Binance Spot (exchangeInfo).")
        return

    async with get_session() as session:
        if not await _check_manual_rate_limit(session, settings, now):
            print(
                f"Rechazado: límite de {settings.manual_max_per_hour} análisis "
                "manuales por hora alcanzado (MANUAL_MAX_PER_HOUR)."
            )
            return

        # --- Descarga (siempre fresca) + persistencia point-in-time ---
        btc_1h = await asyncio.to_thread(client.fetch_klines, BTC_ASSET, "1h", 500)
        btc_4h = await asyncio.to_thread(client.fetch_klines, BTC_ASSET, "4h", 500)
        asset_1h = await asyncio.to_thread(client.fetch_klines, asset, "1h", 500)
        asset_4h = await asyncio.to_thread(client.fetch_klines, asset, "4h", 500)
        [snapshot] = await asyncio.to_thread(client.fetch_ticker_24h, [asset])

        await upsert_asset(session, BTC_ASSET)
        await upsert_asset(session, asset)
        await upsert_candles(session, btc_1h)
        await upsert_candles(session, btc_4h)
        await upsert_candles(session, asset_1h)
        await upsert_candles(session, asset_4h)
        await session.commit()

        if len(asset_1h) < settings.manual_min_candles_1h:
            print(
                f"Aviso: solo hay {len(asset_1h)} velas 1h disponibles "
                f"(mínimo esperado {settings.manual_min_candles_1h}); "
                "el informe continúa con lo disponible (fail-closed en los "
                "checks que lo requieran)."
            )

        # --- Régimen BTC (sección 8.3) ---
        btc_4h_df = candles_to_frame(btc_4h, now=now)
        regime, regime_details = compute_btc_regime(btc_4h_df)
        session.add(
            RegimeLog(
                ts=now,
                btc_regime=regime.value,
                atr_pct=Decimal(str(regime_details.get("atr_pct", 0))),
                details_jsonb=regime_details,
            )
        )
        regime_blocks = blocks_new_entries(regime)

        print(f"\nRégimen BTC (4h): {regime.value}  {'[BLOQUEA ENTRADAS]' if regime_blocks else ''}")
        for k, v in regime_details.items():
            print(f"  - {k}: {v}")

        # --- Filtros duros (sección 8.2) ---
        asset_1h_df = candles_to_frame(asset_1h, now=now)
        asset_4h_df = candles_to_frame(asset_4h, now=now)

        breakout_1h = detect_range_breakout(
            asset_1h_df, settings.range_lookback_candles, float(settings.volume_confirm_mult)
        )
        breakout_4h = detect_range_breakout(
            asset_4h_df, settings.range_lookback_candles, float(settings.volume_confirm_mult)
        )
        breakout_detected = breakout_1h is not None or breakout_4h is not None

        latest_open_time = asset_1h_df["open_time"].iloc[-1] if len(asset_1h_df) else now
        hard_filters = run_hard_filters(
            latest_candle_open_time=latest_open_time,
            timeframe="1h",
            now=now,
            quote_vol_24h=snapshot.quote_vol_24h,
            spread_bps=snapshot.spread_bps,
            change_24h_pct=snapshot.change_24h_pct,
            breakout_detected=breakout_detected,
            settings=settings,
        )

        print("\nFiltros duros del scanner:")
        for name, passed in hard_filters.checks.items():
            print(f"  [{'OK' if passed else 'FALLA'}] {name}")

        scanner_payload = {
            "checks": hard_filters.checks,
            "quote_vol_24h": str(snapshot.quote_vol_24h),
            "spread_bps": str(snapshot.spread_bps),
            "change_24h_pct": str(snapshot.change_24h_pct),
            "breakout_1h": _jsonable_breakout(breakout_1h),
            "breakout_4h": _jsonable_breakout(breakout_4h),
        }

        technical_signal = None
        risk_verdict = None
        rejection_reasons: list[RejectionReason] = list(hard_filters.rejection_reasons)

        if hard_filters.passed:
            # 4h primero (más relevante para swing corto), luego 1h.
            technical_signal = build_technical_signal(
                asset, "4h", asset_4h_df, regime, settings, now
            ) or build_technical_signal(asset, "1h", asset_1h_df, regime, settings, now)

            if technical_signal is None:
                rejection_reasons.append(RejectionReason.no_setup)
                print("\nNo se detectó ningún setup de ruptura de rango confirmado por volumen.")
            else:
                print(f"\nSetup técnico detectado: {technical_signal.setup_type} "
                      f"({technical_signal.timeframe}, conviction={technical_signal.conviction.value})")
                print(f"  entry_zone: {technical_signal.entry_zone}")
                print(f"  stop_loss: {technical_signal.stop_loss}")
                print(f"  take_profit: {technical_signal.take_profit}")
                print(f"  atr_14: {technical_signal.atr_14}")
                print(f"  rel_volume: {technical_signal.rel_volume}")

                entry_ref = (technical_signal.entry_zone[0] + technical_signal.entry_zone[1]) / Decimal("2")
                min_notional = await asyncio.to_thread(client.get_min_notional, asset)
                portfolio = await build_portfolio_snapshot(session, settings, asset, now)

                risk_verdict = evaluate_risk(
                    RiskInput(
                        asset=asset,
                        entry=entry_ref,
                        stop_loss=technical_signal.stop_loss,
                        take_profit=technical_signal.take_profit,
                        atr_14=technical_signal.atr_14,
                        spread_bps=snapshot.spread_bps,
                        min_notional=min_notional,
                        regime_blocks_entries=regime_blocks,
                    ),
                    portfolio,
                    settings,
                )
                rejection_reasons.extend(risk_verdict.rejection_reasons)

                print("\nRisk engine — checklist completo:")
                for name, passed in risk_verdict.checks.items():
                    print(f"  [{'OK' if passed else 'FALLA'}] {name}")
                print(f"\n  approved={risk_verdict.approved}")
                print(f"  rr_net_of_fees={risk_verdict.rr_net_of_fees}")
                print(f"  size_quote={risk_verdict.size_quote}")

        # --- Decisión final ---
        if not operar:
            final_action = FinalAction.watchlist
        elif technical_signal is None or risk_verdict is None or not risk_verdict.approved:
            final_action = FinalAction.reject
        else:
            # DECISION: el executor (OCO en testnet, sección 10) todavía no
            # está implementado en este build. Fail-closed: no se envía
            # ninguna orden aunque el risk engine apruebe.
            final_action = FinalAction.watchlist
            print(
                "\n>>> El risk engine APRUEBA esta operación, pero el executor "
                "(binance_executor.py) todavía no está implementado en este "
                "build. No se ha enviado ninguna orden real. <<<"
            )

        record = DecisionRecord(
            ts=now,
            mode=settings.mode,
            trigger=Trigger.manual,
            asset=asset,
            git_sha=get_git_sha(),
            scanner=scanner_payload,
            technical=technical_signal.model_dump(mode="json") if technical_signal else None,
            fundamental=None,
            decision=None,
            risk_verdict=risk_verdict.model_dump(mode="json") if risk_verdict else None,
            final_action=final_action,
            rejection_reasons=rejection_reasons,
            expected_tp=technical_signal.take_profit if technical_signal else None,
            expected_sl=technical_signal.stop_loss if technical_signal else None,
            horizon_class=technical_signal.horizon_class if technical_signal else None,
        )
        decision_log_id = await log_decision(session, record)
        await session.commit()

        print(f"\nfinal_action: {final_action.value}")
        print(f"rejection_reasons: {[r.value for r in rejection_reasons]}")
        print(f"decision_log_id: {decision_log_id}")
        print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Equivalente CLI de /analiza")
    parser.add_argument("asset", help="Par, ej. SOLUSDT")
    parser.add_argument(
        "modo", nargs="?", default="informe", choices=["informe", "operar"],
        help="'operar' para respetar el risk engine y (si está implementado) ejecutar",
    )
    args = parser.parse_args()
    asyncio.run(analiza(args.asset, operar=(args.modo == "operar")))


if __name__ == "__main__":
    main()
