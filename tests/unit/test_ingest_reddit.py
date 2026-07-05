"""Parseo de listados de Reddit (sección 12.1/12.2, fase 2) — sin red,
contra una fixture grabada, mismo patrón que `test_ingest_rss.py`. También
confirma que sin credenciales `ingest_all` no intenta nada por red."""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from services.fundamental.ingest_reddit import ingest_all, parse_reddit_listing

FIXTURES = Path(__file__).parent.parent / "fixtures"
NOW = datetime(2026, 7, 3, tzinfo=UTC)


def test_parse_reddit_listing_from_fixture():
    raw = json.loads((FIXTURES / "reddit_listing_sample.json").read_text())
    items = parse_reddit_listing("Bitcoin", raw, NOW)

    assert len(items) == 2
    breakout_post, daily_thread = items

    assert breakout_post.platform == "reddit"
    assert breakout_post.subreddit == "Bitcoin"
    assert breakout_post.post_id == "abc123"
    assert breakout_post.title == "Bitcoin just broke a new resistance level"
    assert breakout_post.score_at_fetch == 150
    assert breakout_post.num_comments_at_fetch == 42
    assert breakout_post.fetched_at == NOW
    assert breakout_post.published_at == datetime.fromtimestamp(1751328000, tz=UTC)

    assert daily_thread.post_id == "def456"
    assert daily_thread.body_text is None  # selftext vacío -> None, no ""


async def test_ingest_all_noop_without_credentials(monkeypatch):
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError("no debería llamar a la red sin credenciales")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    settings = Settings(_env_file=None, reddit_client_id="", reddit_client_secret="")

    counts = await ingest_all(object(), settings, now=NOW)  # type: ignore[arg-type]

    assert counts == {}
