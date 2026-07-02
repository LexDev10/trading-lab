"""Filtros duros del scanner — sección 8.2. Orden barato→caro. TODOS deben
pasar. Se evalúan sin cortocircuitar (igual que el risk engine, sección 9)
para que el `decision_log` refleje el resultado de cada check."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.config import Settings
from core.enums import RejectionReason

TIMEFRAME_HOURS = {"1h": 1, "4h": 4}


@dataclass
class HardFilterResult:
    passed: bool
    checks: dict[str, bool]
    rejection_reasons: list[RejectionReason] = field(default_factory=list)


def run_hard_filters(
    *,
    latest_candle_open_time: datetime,
    timeframe: str,
    now: datetime,
    quote_vol_24h: Decimal,
    spread_bps: Decimal,
    change_24h_pct: Decimal,
    breakout_detected: bool,
    settings: Settings,
) -> HardFilterResult:
    max_age = timedelta(hours=2 * TIMEFRAME_HOURS[timeframe])
    fresh = (now - latest_candle_open_time) < max_age

    liquid = quote_vol_24h >= settings.min_quote_vol_24h
    spread_ok = spread_bps <= settings.max_spread_bps
    moved = abs(change_24h_pct) >= settings.min_abs_change_pct or breakout_detected

    checks = {
        "fresh_data": fresh,
        "liquidity": liquid,
        "spread": spread_ok,
        "movement": moved,
    }

    reasons: list[RejectionReason] = []
    if not fresh:
        reasons.append(RejectionReason.stale_data)
    if not liquid:
        reasons.append(RejectionReason.liquidity)
    if not spread_ok:
        reasons.append(RejectionReason.spread)
    if not moved:
        reasons.append(RejectionReason.no_setup)

    return HardFilterResult(passed=all(checks.values()), checks=checks, rejection_reasons=reasons)
