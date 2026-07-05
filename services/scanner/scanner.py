"""Pipeline compartido (scanner + técnico + risk engine) para UN activo ya
con sus datos cargados en memoria. Lo usan tanto el ciclo automático
(`run_scan_cycle`, trigger=scheduled) como el comando manual
`scripts/analiza.py` (trigger=manual) — MISMA función, sin duplicar lógica
(regla crítica, sección 6).

# DECISION (fase 1): cuando el risk engine aprueba (`would_enter_no_executor
# =True`), el caller abre una posición de PAPEL vía
# `services/execution/paper_ledger.py` (simulación sobre velas reales, sin
# exchange) y registra `final_action=enter` — sustituto temporal del
# executor OCO real contra testnet mientras no existan credenciales. Ver
# el docstring de `paper_ledger.py` para el detalle completo."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.enums import FinalAction, RejectionReason, Trigger
from core.enums import Regime
from core.git_info import get_git_sha
from core.logging import get_logger
from core.schemas.decision import DecisionRecord
from core.schemas.market import MarketSnapshot
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from db.models import RegimeLog
from journal.decision_logger import log_decision
from services.data.binance_market_data import BinanceMarketData
from services.data.persistence import get_latest_snapshot, get_recent_candles
from services.execution import paper_ledger
from services.risk.engine import RiskInput, evaluate_risk
from services.risk.portfolio_state import build_portfolio_snapshot
from services.scanner.filters import run_hard_filters
from services.scanner.regime import blocks_new_entries, compute_btc_regime
from services.technical.indicators import candles_to_frame
from services.technical.setups import detect_range_breakout
from services.technical.signal_builder import build_technical_signal

BTC_ASSET = "BTCUSDT"

logger = get_logger("scanner")


@dataclass
class AssetEvaluation:
    scanner_payload: dict[str, Any]
    technical_signal: TechnicalSignal | None
    risk_verdict: RiskVerdict | None
    rejection_reasons: list[RejectionReason] = field(default_factory=list)
    entry_price: Decimal | None = None


def _jsonable_breakout(detection: dict[str, Any] | None) -> dict[str, Any] | None:
    if detection is None:
        return None
    return {**detection, "open_time": str(detection["open_time"]), "close_time": str(detection["close_time"])}


async def evaluate_regime(session: AsyncSession, btc_4h_df: pd.DataFrame, now: datetime) -> tuple[Regime, dict, bool]:
    regime, regime_details = compute_btc_regime(btc_4h_df)
    session.add(
        RegimeLog(
            ts=now,
            btc_regime=regime.value,
            atr_pct=Decimal(str(regime_details.get("atr_pct", 0))),
            details_jsonb=regime_details,
        )
    )
    return regime, regime_details, blocks_new_entries(regime)


async def evaluate_asset(
    *,
    session: AsyncSession,
    client: BinanceMarketData,
    asset: str,
    asset_1h_df: pd.DataFrame,
    asset_4h_df: pd.DataFrame,
    snapshot: MarketSnapshot,
    regime: Regime,
    regime_blocks: bool,
    settings: Settings,
    now: datetime,
) -> AssetEvaluation:
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

    scanner_payload: dict[str, Any] = {
        "checks": hard_filters.checks,
        "quote_vol_24h": str(snapshot.quote_vol_24h),
        "spread_bps": str(snapshot.spread_bps),
        "change_24h_pct": str(snapshot.change_24h_pct),
        "breakout_1h": _jsonable_breakout(breakout_1h),
        "breakout_4h": _jsonable_breakout(breakout_4h),
    }

    technical_signal: TechnicalSignal | None = None
    risk_verdict: RiskVerdict | None = None
    entry_price: Decimal | None = None
    rejection_reasons: list[RejectionReason] = list(hard_filters.rejection_reasons)

    if hard_filters.passed:
        # 4h primero (más relevante para swing corto), luego 1h.
        technical_signal = build_technical_signal(
            asset, "4h", asset_4h_df, regime, settings, now
        ) or build_technical_signal(asset, "1h", asset_1h_df, regime, settings, now)

        if technical_signal is None:
            rejection_reasons.append(RejectionReason.no_setup)
        else:
            entry_ref = (technical_signal.entry_zone[0] + technical_signal.entry_zone[1]) / Decimal("2")
            entry_price = entry_ref
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

    return AssetEvaluation(
        scanner_payload=scanner_payload,
        technical_signal=technical_signal,
        risk_verdict=risk_verdict,
        rejection_reasons=rejection_reasons,
        entry_price=entry_price,
    )


def decide_final_action(
    mode: Literal["informe", "operate"],
    technical_signal: TechnicalSignal | None,
    risk_verdict: RiskVerdict | None,
) -> tuple[FinalAction, bool]:
    """`would_enter_no_executor`: True si el risk engine aprobaría la
    entrada pero el executor (sección 10) todavía no existe en este
    build — fail-closed, nunca se envía una orden real."""
    if mode == "informe":
        return FinalAction.watchlist, False
    if technical_signal is None or risk_verdict is None or not risk_verdict.approved:
        return FinalAction.reject, False
    # DECISION: el executor (OCO en testnet) todavía no está implementado.
    return FinalAction.watchlist, True


async def run_scan_cycle(session: AsyncSession, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Ciclo automático (`trigger=scheduled`) sobre todo `UNIVERSE`. Lee
    velas/snapshots YA persistidos por `ingest_market_data_job` — no
    vuelve a golpear la API de Binance salvo para `exchangeInfo`
    (min_notional), que solo se pide cuando hay un setup real.

    Se comporta como el modo "operar" de `/analiza`: si el risk engine
    aprueba, se abre una posición de PAPEL (sin exchange real, ver
    `services/execution/paper_ledger.py`) y se registra `final_action=enter`."""
    now = now or datetime.now(tz=UTC)
    client = BinanceMarketData()
    git_sha = get_git_sha()
    log = logger.bind(job="scan_cycle", started_at=now.isoformat())
    log.info("cycle.start", universe_size=len(settings.universe_list))

    counts = {"enter_would": 0, "reject": 0, "watchlist_no_signal": 0}

    btc_4h = await get_recent_candles(session, BTC_ASSET, "4h", 500)
    btc_4h_df = candles_to_frame(btc_4h, now=now)
    regime, _regime_details, regime_blocks = await evaluate_regime(session, btc_4h_df, now)

    for asset in settings.universe_list:
        asset_1h = await get_recent_candles(session, asset, "1h", 500)
        asset_4h = await get_recent_candles(session, asset, "4h", 500)
        snapshot = await get_latest_snapshot(session, asset)

        if not asset_1h or snapshot is None:
            # Fail-closed: sin datos frescos del activo, no se evalúa.
            record = DecisionRecord(
                ts=now,
                mode=settings.mode,
                trigger=Trigger.scheduled,
                asset=asset,
                git_sha=git_sha,
                scanner={"reason": "no_data_available"},
                final_action=FinalAction.reject,
                rejection_reasons=[RejectionReason.stale_data],
            )
            await log_decision(session, record)
            counts["reject"] += 1
            continue

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

        final_action, would_enter_no_executor = decide_final_action(
            "operate", evaluation.technical_signal, evaluation.risk_verdict
        )
        if would_enter_no_executor:
            final_action = FinalAction.enter
            counts["enter_would"] += 1
        elif final_action == FinalAction.reject:
            counts["reject"] += 1
        else:
            counts["watchlist_no_signal"] += 1

        technical_signal = evaluation.technical_signal
        record = DecisionRecord(
            ts=now,
            mode=settings.mode,
            trigger=Trigger.scheduled,
            asset=asset,
            git_sha=git_sha,
            scanner=evaluation.scanner_payload,
            technical=technical_signal.model_dump(mode="json") if technical_signal else None,
            risk_verdict=evaluation.risk_verdict.model_dump(mode="json") if evaluation.risk_verdict else None,
            final_action=final_action,
            rejection_reasons=evaluation.rejection_reasons,
            expected_tp=technical_signal.take_profit if technical_signal else None,
            expected_sl=technical_signal.stop_loss if technical_signal else None,
            horizon_class=technical_signal.horizon_class if technical_signal else None,
        )
        decision_log_id = await log_decision(session, record)

        if would_enter_no_executor:
            assert technical_signal is not None and evaluation.risk_verdict is not None and evaluation.entry_price is not None
            await paper_ledger.open_position(
                session,
                settings,
                decision_log_id=decision_log_id,
                asset=asset,
                technical_signal=technical_signal,
                risk_verdict=evaluation.risk_verdict,
                entry_price=evaluation.entry_price,
                now=now,
            )
            log.info("cycle.paper_open", asset=asset, decision_log_id=decision_log_id)

    log.info("cycle.success", **counts)
    return counts
