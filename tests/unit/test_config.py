from decimal import Decimal

from app.config import Settings


def test_defaults_match_appendix_a():
    settings = Settings(_env_file=None)

    assert settings.environment == "testnet"
    assert settings.mode == "technical_only"
    assert settings.universe_list == [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    ]
    assert settings.core_assets_list == ["BTCUSDT", "ETHUSDT"]
    assert settings.scan_interval_minutes == 15
    assert settings.min_quote_vol_24h == Decimal("50000000")
    assert settings.max_spread_bps == Decimal("5")
    assert settings.risk_per_trade == Decimal("0.005")
    assert settings.max_positions == 3
    assert settings.min_rr_net == Decimal("1.8")
    assert settings.live_max_capital == Decimal("0")


def test_universe_parsing_strips_whitespace():
    settings = Settings(_env_file=None, universe="BTCUSDT, ETHUSDT , SOLUSDT")
    assert settings.universe_list == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
