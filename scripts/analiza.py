"""Equivalente CLI de `/analiza <PAR> [operar]` — sección 21.2.

Uso:
    python -m scripts.analiza SOLUSDT              # modo informe (nunca ejecuta)
    python -m scripts.analiza SOLUSDT operar        # modo operar (respeta risk engine)

Ambos modos corren el pipeline completo (scanner + técnico + risk engine),
vía `services.scanner.scanner.evaluate_asset` — la MISMA función que usa el
ciclo automático (`run_scan_cycle`), para no duplicar lógica de señales
(regla crítica, sección 6). Aquí solo vive lo específico del modo manual:
descarga on-demand fuera del universo (sección 21.3), límite de tasa y
rechazo de stablecoins (sección 21.5).

El modo informe SIEMPRE registra `final_action=watchlist` y nunca ejecuta.
El modo operar registra `final_action=enter` si el risk engine aprueba,
abriendo una posición de PAPEL (simulación sobre velas reales, sin
exchange — ver `services/execution/paper_ledger.py`); nunca se envía
ninguna orden real (el executor OCO contra testnet sigue bloqueado por
credenciales).
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.config import Settings, get_settings
from core.enums import FinalAction, Trigger
from core.git_info import get_git_sha
from core.schemas.decision import DecisionRecord
from db.models import DecisionLog
from db.session import get_session
from journal.decision_logger import log_decision
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import upsert_asset, upsert_candles
from services.execution import paper_ledger
from services.scanner.scanner import BTC_ASSET, decide_final_action, evaluate_asset, evaluate_regime
from services.technical.indicators import candles_to_frame

STABLECOIN_BASES = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDP"}


async def _check_manual_rate_limit(session, settings: Settings, now: datetime) -> bool:
    stmt = select(func.count(DecisionLog.id)).where(
        DecisionLog.trigger == Trigger.manual.value,
        DecisionLog.ts >= now - timedelta(hours=1),
    )
    result = await session.execute(stmt)
    count = result.scalar_one()
    return count < settings.manual_max_per_hour


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
        regime, regime_details, regime_blocks = await evaluate_regime(session, btc_4h_df, now)

        print(f"\nRégimen BTC (4h): {regime.value}  {'[BLOQUEA ENTRADAS]' if regime_blocks else ''}")
        for k, v in regime_details.items():
            print(f"  - {k}: {v}")

        # --- Pipeline compartido: filtros duros + técnico + risk engine ---
        asset_1h_df = candles_to_frame(asset_1h, now=now)
        asset_4h_df = candles_to_frame(asset_4h, now=now)

        evaluation = await evaluate_asset(
            session=session,
            client=client,
            asset=asset,
            asset_1h_df=asset_1h_df,
            asset_4h_df=asset_4h_df,
            snapshot=snapshot,
            regime=regime,
            regime_blocks=regime_blocks,
            settings=settings,
            now=now,
        )

        print("\nFiltros duros del scanner:")
        for name, passed in evaluation.scanner_payload["checks"].items():
            print(f"  [{'OK' if passed else 'FALLA'}] {name}")

        technical_signal = evaluation.technical_signal
        risk_verdict = evaluation.risk_verdict

        if technical_signal is None:
            print("\nNo se detectó ningún setup de ruptura de rango confirmado por volumen"
                  " (o los filtros duros no pasaron).")
        else:
            print(f"\nSetup técnico detectado: {technical_signal.setup_type} "
                  f"({technical_signal.timeframe}, conviction={technical_signal.conviction.value})")
            print(f"  entry_zone: {technical_signal.entry_zone}")
            print(f"  stop_loss: {technical_signal.stop_loss}")
            print(f"  take_profit: {technical_signal.take_profit}")
            print(f"  atr_14: {technical_signal.atr_14}")
            print(f"  rel_volume: {technical_signal.rel_volume}")

            if risk_verdict is not None:
                print("\nRisk engine — checklist completo:")
                for name, passed in risk_verdict.checks.items():
                    print(f"  [{'OK' if passed else 'FALLA'}] {name}")
                print(f"\n  approved={risk_verdict.approved}")
                print(f"  rr_net_of_fees={risk_verdict.rr_net_of_fees}")
                print(f"  size_quote={risk_verdict.size_quote}")

            if evaluation.policy_outcome is not None:
                print(
                    f"\nMeta-decider (MODE={settings.mode}): stance="
                    f"{evaluation.fundamental_stance.value if evaluation.fundamental_stance else '?'} "
                    f"-> {evaluation.policy_outcome.action} "
                    f"(size x{evaluation.policy_outcome.size_multiplier})"
                )

        # --- Decisión final ---
        mode = "operate" if operar else "informe"
        final_action, would_enter_no_executor = decide_final_action(
            mode, technical_signal, risk_verdict, evaluation.policy_outcome
        )
        if would_enter_no_executor:
            final_action = FinalAction.enter

        decision_payload = None
        if evaluation.policy_outcome is not None:
            decision_payload = {
                "fundamental_stance": evaluation.fundamental_stance.value if evaluation.fundamental_stance else None,
                "policy_action": evaluation.policy_outcome.action,
                "size_multiplier": str(evaluation.policy_outcome.size_multiplier),
            }

        record = DecisionRecord(
            ts=now,
            mode=settings.mode,
            trigger=Trigger.manual,
            asset=asset,
            git_sha=get_git_sha(),
            scanner=evaluation.scanner_payload,
            technical=technical_signal.model_dump(mode="json") if technical_signal else None,
            decision=decision_payload,
            risk_verdict=risk_verdict.model_dump(mode="json") if risk_verdict else None,
            final_action=final_action,
            rejection_reasons=evaluation.rejection_reasons,
            expected_tp=technical_signal.take_profit if technical_signal else None,
            expected_sl=technical_signal.stop_loss if technical_signal else None,
            horizon_class=technical_signal.horizon_class if technical_signal else None,
        )
        decision_log_id = await log_decision(session, record)

        if would_enter_no_executor:
            assert technical_signal is not None and risk_verdict is not None and evaluation.entry_price is not None
            risk_verdict_for_entry = risk_verdict
            outcome = evaluation.policy_outcome
            if outcome is not None and outcome.action == "enter" and outcome.size_multiplier != Decimal("1"):
                assert risk_verdict_for_entry.size_quote is not None
                risk_verdict_for_entry = risk_verdict_for_entry.model_copy(
                    update={"size_quote": risk_verdict_for_entry.size_quote * outcome.size_multiplier}
                )
            await paper_ledger.open_position(
                session,
                settings,
                decision_log_id=decision_log_id,
                asset=asset,
                technical_signal=technical_signal,
                risk_verdict=risk_verdict,
                entry_price=evaluation.entry_price,
                now=now,
            )
            print(
                "\n>>> El risk engine APRUEBA esta operación: se abre una "
                "posición de PAPEL (simulación sobre velas reales, sin "
                "exchange). No se ha enviado ninguna orden real. <<<"
            )

        await session.commit()

        print(f"\nfinal_action: {final_action.value}")
        print(f"rejection_reasons: {[r.value for r in evaluation.rejection_reasons]}")
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
