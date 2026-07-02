from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.config import Settings
from core.enums import RejectionReason
from services.scanner.filters import run_hard_filters


def _settings() -> Settings:
    return Settings(_env_file=None)


def _base_kwargs(now: datetime) -> dict:
    return dict(
        latest_candle_open_time=now - timedelta(minutes=30),
        timeframe="1h",
        now=now,
        quote_vol_24h=Decimal("100000000"),
        spread_bps=Decimal("2"),
        change_24h_pct=Decimal("5.0"),
        breakout_detected=False,
        settings=_settings(),
    )


def test_all_hard_filters_pass():
    now = datetime.now(tz=UTC)
    result = run_hard_filters(**_base_kwargs(now))
    assert result.passed is True
    assert result.rejection_reasons == []


def test_stale_data_fails():
    now = datetime.now(tz=UTC)
    kwargs = _base_kwargs(now)
    kwargs["latest_candle_open_time"] = now - timedelta(hours=5)
    result = run_hard_filters(**kwargs)
    assert result.passed is False
    assert RejectionReason.stale_data in result.rejection_reasons


def test_liquidity_fails():
    now = datetime.now(tz=UTC)
    kwargs = _base_kwargs(now)
    kwargs["quote_vol_24h"] = Decimal("1000")
    result = run_hard_filters(**kwargs)
    assert result.passed is False
    assert RejectionReason.liquidity in result.rejection_reasons


def test_spread_fails():
    now = datetime.now(tz=UTC)
    kwargs = _base_kwargs(now)
    kwargs["spread_bps"] = Decimal("50")
    result = run_hard_filters(**kwargs)
    assert result.passed is False
    assert RejectionReason.spread in result.rejection_reasons


def test_movement_fails_without_change_or_breakout():
    now = datetime.now(tz=UTC)
    kwargs = _base_kwargs(now)
    kwargs["change_24h_pct"] = Decimal("0.5")
    kwargs["breakout_detected"] = False
    result = run_hard_filters(**kwargs)
    assert result.passed is False
    assert RejectionReason.no_setup in result.rejection_reasons


def test_movement_passes_via_breakout_even_with_low_change():
    now = datetime.now(tz=UTC)
    kwargs = _base_kwargs(now)
    kwargs["change_24h_pct"] = Decimal("0.5")
    kwargs["breakout_detected"] = True
    result = run_hard_filters(**kwargs)
    assert result.checks["movement"] is True
