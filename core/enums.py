"""Todos los enums del sistema. Ver sección 7.1 y 21.1 de
ESPECIFICACION_SISTEMA_TRADING.md."""

from enum import Enum


class Regime(str, Enum):
    trend_up = "trend_up"
    trend_down = "trend_down"
    range = "range"
    chop_high_vol = "chop_high_vol"


class HorizonClass(str, Enum):
    hours = "hours"
    days = "days"


class SignalDirection(str, Enum):
    long = "long"
    none = "none"


class TechnicalConviction(str, Enum):
    strong = "strong"
    moderate = "moderate"
    weak = "weak"


class FundamentalStance(str, Enum):
    bullish_strong = "bullish_strong"
    bullish_weak = "bullish_weak"
    neutral = "neutral"
    bearish_weak = "bearish_weak"
    bearish_strong = "bearish_strong"
    unknown = "unknown"


class EventType(str, Enum):
    exchange_listing = "exchange_listing"
    delisting = "delisting"
    regulatory = "regulatory"
    hack_exploit = "hack_exploit"
    etf_flows = "etf_flows"
    unlock = "unlock"
    partnership = "partnership"
    macro = "macro"
    other = "other"


class FinalAction(str, Enum):
    enter = "enter"
    reject = "reject"
    watchlist = "watchlist"


class RejectionReason(str, Enum):
    regime_filter = "regime_filter"
    liquidity = "liquidity"
    spread = "spread"
    rr_too_low = "rr_too_low"
    max_positions = "max_positions"
    correlated_exposure = "correlated_exposure"
    daily_loss_limit = "daily_loss_limit"
    drawdown_killswitch = "drawdown_killswitch"
    cooldown = "cooldown"
    contract_violation = "contract_violation"
    fundamental_veto = "fundamental_veto"
    exchange_filter = "exchange_filter"
    stale_data = "stale_data"
    execution_error = "execution_error"
    no_setup = "no_setup"
    sl_distance_invalid = "sl_distance_invalid"


class TradeStatus(str, Enum):
    pending = "pending"
    open = "open"
    closed_tp = "closed_tp"
    closed_sl = "closed_sl"
    closed_manual = "closed_manual"
    closed_invalidated = "closed_invalidated"
    closed_time = "closed_time"
    # Valor corto a propósito: trade_entries.status/trade_exits.exit_type
    # son String(20) — "closed_fundamental_veto" (24 chars) no entra.
    closed_fundamental_veto = "closed_veto"
    error = "error"


class Trigger(str, Enum):
    scheduled = "scheduled"
    manual = "manual"


class SystemState(str, Enum):
    running = "running"
    halt = "halt"
