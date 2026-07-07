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

# DECISION (2026-07-07, fase 3 dashboard): `analiza()` (CLI, imprime) se
# separó de `run_manual_analysis()` (sin prints, devuelve un
# `ManualAnalysisResult`) para que `dashboard/` pueda ofrecer el mismo
# botón "Analizar ahora" reutilizando ESTA función — una sola
# implementación (regla crítica, sección 6), el CLI es ahora un wrapper
# fino que solo formatea la salida de consola a partir del resultado.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.config import Settings, get_settings
from core.enums import FinalAction, FundamentalStance, Regime, Trigger
from core.git_info import get_git_sha
from core.schemas.decision import DecisionRecord
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from db.models import DecisionLog
from db.session import get_session
from journal.decision_logger import log_decision
from notifications.telegram import send_message
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


@dataclass
class ManualAnalysisResult:
    """Resultado sin formatear de `run_manual_analysis` — el CLI
    (`analiza`) lo imprime tal cual lo hacía antes de la fase 3;
    `dashboard/` lo pinta con sus propios componentes visuales. Ningún
    campo aquí implica una decisión nueva: es la misma información que
    ya calculaba `evaluate_asset`/`decide_final_action`, solo capturada
    en vez de impresa."""

    asset: str
    trigger_mode: str
    system_mode: str
    early_rejection: str | None = None
    warnings: list[str] = field(default_factory=list)
    regime: Regime | None = None
    regime_details: dict[str, Any] | None = None
    regime_blocks: bool | None = None
    scanner_checks: dict[str, bool] | None = None
    technical_signal: TechnicalSignal | None = None
    risk_verdict: RiskVerdict | None = None
    fundamental_stance: FundamentalStance | None = None
    policy_action: str | None = None
    policy_size_multiplier: Decimal | None = None
    final_action: FinalAction | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    decision_log_id: int | None = None
    opened_position: bool = False
    open_message: str | None = None


async def run_manual_analysis(asset: str, operar: bool) -> ManualAnalysisResult:
    """Núcleo del modo manual — sin ningún `print()`. Descarga datos
    frescos, corre el pipeline compartido (`evaluate_asset`) y, si
    corresponde, registra la decisión y (en modo operar, si el risk
    engine aprueba) abre una posición de papel. Devuelve un
    `ManualAnalysisResult` con todo lo que antes solo se imprimía."""
    settings = get_settings()
    now = datetime.now(tz=UTC)
    asset = asset.upper().strip()
    trigger_mode = "operar" if operar else "informe"
    result = ManualAnalysisResult(asset=asset, trigger_mode=trigger_mode, system_mode=settings.mode)

    if not asset.endswith("USDT"):
        result.early_rejection = "Rechazado: en el MVP solo se admiten pares cotizados en USDT."
        return result

    base = asset.removesuffix("USDT")
    if base in STABLECOIN_BASES or asset in STABLECOIN_BASES:
        result.early_rejection = (
            "Las stablecoins no son activos operables: sin volatilidad "
            "direccional no hay setup posible (sección 21.5). No se "
            "realiza análisis."
        )
        return result

    client = BinanceMarketData()

    exists = await asyncio.to_thread(client.symbol_exists, asset)
    if not exists:
        result.early_rejection = f"Rechazado: {asset} no existe en Binance Spot (exchangeInfo)."
        return result

    async with get_session() as session:
        if not await _check_manual_rate_limit(session, settings, now):
            result.early_rejection = (
                f"Rechazado: límite de {settings.manual_max_per_hour} análisis "
                "manuales por hora alcanzado (MANUAL_MAX_PER_HOUR)."
            )
            return result

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
            result.warnings.append(
                f"Solo hay {len(asset_1h)} velas 1h disponibles "
                f"(mínimo esperado {settings.manual_min_candles_1h}); "
                "el informe continúa con lo disponible (fail-closed en los "
                "checks que lo requieran)."
            )

        # --- Régimen BTC (sección 8.3) ---
        btc_4h_df = candles_to_frame(btc_4h, now=now)
        regime, regime_details, regime_blocks = await evaluate_regime(session, btc_4h_df, now)
        result.regime = regime
        result.regime_details = regime_details
        result.regime_blocks = regime_blocks

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
        result.scanner_checks = evaluation.scanner_payload["checks"]

        technical_signal = evaluation.technical_signal
        risk_verdict = evaluation.risk_verdict
        result.technical_signal = technical_signal
        result.risk_verdict = risk_verdict
        if evaluation.policy_outcome is not None:
            result.fundamental_stance = evaluation.fundamental_stance
            result.policy_action = evaluation.policy_outcome.action
            result.policy_size_multiplier = evaluation.policy_outcome.size_multiplier

        # --- Decisión final ---
        mode = "operate" if operar else "informe"
        final_action, would_enter_no_executor = decide_final_action(
            mode, technical_signal, risk_verdict, evaluation.policy_outcome
        )
        if would_enter_no_executor:
            final_action = FinalAction.enter
        result.final_action = final_action
        result.rejection_reasons = [r.value for r in evaluation.rejection_reasons]

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
        result.decision_log_id = decision_log_id

        open_message: str | None = None
        if would_enter_no_executor:
            assert technical_signal is not None and risk_verdict is not None and evaluation.entry_price is not None
            risk_verdict_for_entry = risk_verdict
            outcome = evaluation.policy_outcome
            if outcome is not None and outcome.action == "enter" and outcome.size_multiplier != Decimal("1"):
                assert risk_verdict_for_entry.size_quote is not None
                risk_verdict_for_entry = risk_verdict_for_entry.model_copy(
                    update={"size_quote": risk_verdict_for_entry.size_quote * outcome.size_multiplier}
                )
            _entry, open_message = await paper_ledger.open_position(
                session,
                settings,
                decision_log_id=decision_log_id,
                asset=asset,
                technical_signal=technical_signal,
                # FIX (2026-07-07, fase 3 dashboard): antes se pasaba
                # `risk_verdict` (sin ajustar) en vez de
                # `risk_verdict_for_entry` — el multiplicador de tamaño
                # del meta-decider (policy_outcome) se calculaba y se
                # descartaba sin aplicarse nunca. `run_scan_cycle` (ciclo
                # automático) ya usaba `risk_verdict_for_entry`
                # correctamente; esto alinea el modo manual con el mismo
                # criterio.
                risk_verdict=risk_verdict_for_entry,
                entry_price=evaluation.entry_price,
                now=now,
            )
            result.opened_position = True
            result.open_message = open_message

        await session.commit()

        if open_message is not None:
            await send_message(settings, open_message)

    return result


async def analiza(asset: str, operar: bool) -> None:
    """Wrapper CLI: llama a `run_manual_analysis` y formatea EXACTAMENTE
    la misma salida de consola que antes de la fase 3 — ningún cambio de
    comportamiento salvo el fix documentado arriba en
    `run_manual_analysis`."""
    normalized_asset = asset.upper().strip()
    trigger_mode = "operar" if operar else "informe"
    _print_header(normalized_asset, trigger_mode)

    result = await run_manual_analysis(asset, operar)

    if result.early_rejection:
        print(result.early_rejection)
        return

    for warning in result.warnings:
        print(f"Aviso: {warning}")

    assert result.regime is not None and result.regime_details is not None
    print(f"\nRégimen BTC (4h): {result.regime.value}  {'[BLOQUEA ENTRADAS]' if result.regime_blocks else ''}")
    for k, v in result.regime_details.items():
        print(f"  - {k}: {v}")

    assert result.scanner_checks is not None
    print("\nFiltros duros del scanner:")
    for name, passed in result.scanner_checks.items():
        print(f"  [{'OK' if passed else 'FALLA'}] {name}")

    technical_signal = result.technical_signal
    risk_verdict = result.risk_verdict

    if technical_signal is None:
        print(
            "\nNo se detectó ningún setup de ruptura de rango confirmado por volumen"
            " (o los filtros duros no pasaron)."
        )
    else:
        print(
            f"\nSetup técnico detectado: {technical_signal.setup_type} "
            f"({technical_signal.timeframe}, conviction={technical_signal.conviction.value})"
        )
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

        if result.policy_action is not None:
            print(
                f"\nMeta-decider (MODE={result.system_mode}): stance="
                f"{result.fundamental_stance.value if result.fundamental_stance else '?'} "
                f"-> {result.policy_action} "
                f"(size x{result.policy_size_multiplier})"
            )

    if result.opened_position:
        print(
            "\n>>> El risk engine APRUEBA esta operación: se registra una "
            "ORDEN PENDIENTE de papel (fill simulado sobre velas reales, "
            "sin exchange). No se ha enviado ninguna orden real. <<<"
        )

    assert result.final_action is not None
    print(f"\nfinal_action: {result.final_action.value}")
    print(f"rejection_reasons: {result.rejection_reasons}")
    print(f"decision_log_id: {result.decision_log_id}")
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
