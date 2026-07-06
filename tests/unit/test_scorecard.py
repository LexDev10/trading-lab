"""Helpers puros del scorecard semanal (sección 12.3/16) — sin DB. El
cómputo completo (`compute_weekly_scorecard`, necesita Postgres real para
leer `item_classifications`/velas) se prueba en
`tests/integration/test_scorecard.py`."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.schemas.market import Candle
from services.fundamental.scorecard import _hit, _price_at_or_before, _week_start


def make_candle(open_time: datetime, close: str) -> Candle:
    return Candle(
        asset="BTCUSDT",
        timeframe="1h",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
    )


def test_week_start_floors_to_monday_utc():
    # 2026-07-08 es miércoles; el lunes de esa semana es 2026-07-06.
    wednesday = datetime(2026, 7, 8, 14, 30, tzinfo=UTC)
    assert _week_start(wednesday) == datetime(2026, 7, 6, tzinfo=UTC)


def test_week_start_on_monday_is_itself():
    monday = datetime(2026, 7, 6, 0, 5, tzinfo=UTC)
    assert _week_start(monday) == datetime(2026, 7, 6, tzinfo=UTC)


def test_price_at_or_before_picks_last_candle_not_after_ts():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = [make_candle(base, "100"), make_candle(base + timedelta(hours=1), "110"), make_candle(base + timedelta(hours=2), "120")]
    # Justo en el open_time de la 2a vela (aún no cerrada del todo, pero
    # la regla es open_time <= ts): coge esa.
    assert _price_at_or_before(candles, base + timedelta(hours=1)) == Decimal("110")
    # Antes de cualquier vela -> ninguna.
    assert _price_at_or_before(candles, base - timedelta(hours=1)) is None
    # Después de todas -> la última.
    assert _price_at_or_before(candles, base + timedelta(hours=10)) == Decimal("120")


def test_hit_bullish_correct_when_return_positive():
    hit, signed = _hit("bullish_strong", Decimal("0.05"))
    assert hit is True
    assert signed == Decimal("0.05")


def test_hit_bullish_wrong_when_return_negative():
    hit, signed = _hit("bullish_weak", Decimal("-0.02"))
    assert hit is False
    assert signed == Decimal("-0.02")


def test_hit_bearish_correct_when_return_negative():
    hit, signed = _hit("bearish_strong", Decimal("-0.03"))
    assert hit is True
    assert signed == Decimal("0.03")  # firmado: acierto bearish -> positivo


def test_hit_bearish_wrong_when_return_positive():
    hit, signed = _hit("bearish_weak", Decimal("0.01"))
    assert hit is False
    assert signed == Decimal("-0.01")


def test_hit_returns_none_for_non_directional_stance():
    assert _hit("neutral", Decimal("0.02")) is None
    assert _hit("unknown", Decimal("0.02")) is None
