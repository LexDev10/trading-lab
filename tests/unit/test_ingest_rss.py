"""Parseo del almacén PIT (sección 12.1/12.2, fase 2) — sin red, contra
fixtures grabadas, mismo patrón que `test_binance_market_data.py`."""

import json
from datetime import UTC, datetime
from pathlib import Path

from services.fundamental.ingest_rss import (
    content_hash,
    extract_asset_tags,
    parse_binance_announcements,
    parse_rss_feed,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = datetime(2026, 7, 3, tzinfo=UTC)


def test_extract_asset_tags_matches_full_name_and_ticker():
    assert extract_asset_tags("Bitcoin surges past $100k") == ["BTC"]
    assert extract_asset_tags("btc and ETH both rally") == ["BTC", "ETH"]


def test_extract_asset_tags_no_match():
    assert extract_asset_tags("Completely unrelated headline about weather") == []


def test_extract_asset_tags_respects_word_boundary():
    # "SOLD" contiene "sol" como substring pero no como palabra completa.
    assert extract_asset_tags("Everything got SOLD off today") == []


def test_content_hash_is_deterministic_and_input_sensitive():
    a = content_hash("coindesk", "https://example.com/1")
    b = content_hash("coindesk", "https://example.com/1")
    c = content_hash("coindesk", "https://example.com/2")
    d = content_hash("theblock", "https://example.com/1")
    assert a == b
    assert a != c
    assert a != d


def test_parse_rss_feed_from_fixture():
    raw = (FIXTURES / "rss_sample.xml").read_bytes()
    items = parse_rss_feed("test_source", raw, NOW)

    assert len(items) == 2
    btc_item, weather_item = items

    assert btc_item.source == "test_source"
    assert btc_item.title == "Bitcoin ETF sees record inflows this week"
    assert btc_item.asset_tags == ["BTC"]
    assert btc_item.fetched_at == NOW
    assert btc_item.published_at == datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)

    assert weather_item.asset_tags == []
    # Distintos items del mismo feed -> distinto content_hash.
    assert btc_item.content_hash != weather_item.content_hash


def test_parse_binance_announcements_from_fixture():
    raw = json.loads((FIXTURES / "binance_announcements_sample.json").read_text())
    items = parse_binance_announcements(raw, NOW)

    assert len(items) == 2
    listing_item, futures_item = items

    assert listing_item.source == "binance_announcements"
    assert "SOL" in listing_item.asset_tags
    assert futures_item.asset_tags == []
    assert listing_item.content_hash != futures_item.content_hash
