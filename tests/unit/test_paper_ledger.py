"""`evaluate_exit` — función pura del paper ledger (sección 11 simplificada,
ver `services/execution/paper_ledger.py`): SL, TP, invalidación técnica y
salida por tiempo, con criterio conservador (SL antes que TP en la misma
vela) y anti look-ahead (velas anteriores o iguales a `entry_time` se
ignoran, igual que `services/technical/indicators.py::candles_to_frame`)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import Settings
from core.schemas.market import Candle
from db.models import TradeEntry
from services.execution.paper_ledger import evaluate_exit

ENTRY_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def base_settings() -> Settings:
    return Settings(_env_file=None)


def base_entry(**overrides) -> TradeEntry:
    defaults = dict(
        entry_time=ENTRY_TIME,
        entry_price=Decimal("100"),
        sl=Decimal("95"),
        tp=Decimal("115"),
        horizon_class="hours",
        invalidation_level=Decimal("98"),
    )
    defaults.update(overrides)
    return TradeEntry(**defaults)


def make_candle(open_time: datetime, *, low: str, high: str, close: str) -> Candle:
    return Candle(
        asset="SOLUSDT",
        timeframe="1h",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1000"),
        quote_volume=Decimal("100000"),
    )


def test_sl_hit():
    entry = base_entry()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="90", high="101", close="94")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is not None
    assert decision.exit_type == "closed_sl"
    assert decision.exit_price == entry.sl


def test_tp_hit():
    entry = base_entry()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="99", high="116", close="116")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is not None
    assert decision.exit_type == "closed_tp"
    assert decision.exit_price == entry.tp


def test_sl_wins_over_tp_in_same_candle():
    entry = base_entry()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="90", high="120", close="110")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is not None
    assert decision.exit_type == "closed_sl"


def test_invalidation_hit_without_touching_sl_or_tp():
    entry = base_entry()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="96", high="100", close="97")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is not None
    assert decision.exit_type == "closed_invalidated"
    assert decision.exit_price == Decimal("97")


def test_no_exit_yet():
    entry = base_entry()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="99", high="110", close="105")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is None


def test_in_progress_candle_is_ignored():
    entry = base_entry()
    # Esta vela tocaría el SL, pero todavía está EN CURSO (close_time > now):
    # no debe evaluarse hasta que cierre — mismo criterio que
    # `candles_to_frame` (FIX 2026-07-06, ver CHANGELOG). La vela abre en
    # +1h y cierra en +2h; `now` está a mitad de la vela (+1h30).
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="90", high="101", close="94")]
    now = ENTRY_TIME + timedelta(hours=1, minutes=30)
    assert evaluate_exit(entry, candles, base_settings(), now) is None


def test_candle_evaluated_once_it_closes():
    entry = base_entry()
    # La misma vela del caso anterior, pero con `now` ya en su close_time:
    # ahora sí debe disparar el SL.
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="90", high="101", close="94")]
    now = ENTRY_TIME + timedelta(hours=2)
    decision = evaluate_exit(entry, candles, base_settings(), now)
    assert decision is not None
    assert decision.exit_type == "closed_sl"


def test_candles_at_or_before_entry_time_are_ignored():
    entry = base_entry()
    # Esta vela SÍ tocaría el SL, pero es la vela que generó la señal
    # (open_time == entry_time) o anterior: no debe contarse (look-ahead).
    candles = [make_candle(ENTRY_TIME, low="90", high="101", close="94")]
    decision = evaluate_exit(entry, candles, base_settings(), ENTRY_TIME + timedelta(hours=2))
    assert decision is None


def test_time_based_exit_after_horizon_hours():
    entry = base_entry(horizon_class="hours")
    settings = base_settings()
    candles = [make_candle(ENTRY_TIME + timedelta(hours=1), low="99", high="110", close="105")]
    now = ENTRY_TIME + timedelta(hours=settings.max_hold_hours_intraday + 1)
    decision = evaluate_exit(entry, candles, settings, now)
    assert decision is not None
    assert decision.exit_type == "closed_time"
    assert decision.exit_price == Decimal("105")


def test_time_based_exit_uses_entry_price_without_any_candle():
    entry = base_entry(horizon_class="hours")
    settings = base_settings()
    now = ENTRY_TIME + timedelta(hours=settings.max_hold_hours_intraday + 1)
    decision = evaluate_exit(entry, [], settings, now)
    assert decision is not None
    assert decision.exit_type == "closed_time"
    assert decision.exit_price == entry.entry_price


def test_time_based_exit_respects_days_horizon():
    entry = base_entry(horizon_class="days")
    settings = base_settings()
    not_yet = ENTRY_TIME + timedelta(hours=settings.max_hold_hours_intraday + 1)
    assert evaluate_exit(entry, [], settings, not_yet) is None

    expired = ENTRY_TIME + timedelta(days=settings.max_hold_days_swing + 1)
    decision = evaluate_exit(entry, [], settings, expired)
    assert decision is not None
    assert decision.exit_type == "closed_time"
