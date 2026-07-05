"""Ingesta al almacén PIT (sección 12.1/12.2, fase 2 — arranque). Fuentes:
anuncios de Binance (endpoint JSON no documentado, ver # DECISION abajo),
CoinDesk y The Block (RSS estándar).

Separación red/parseo (mismo patrón que
`services/data/binance_market_data.py`): las funciones `fetch_*` hacen la
llamada HTTP, `parse_*` son puras y testeables con fixtures grabadas, sin
red.

Reglas del almacén PIT (sección 12.1): nunca UPDATE, solo INSERT
idempotente por `content_hash` (`ON CONFLICT DO NOTHING`)."""

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, cast

import feedparser
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging import get_logger
from core.schemas.fundamental import NewsItem
from db.models import NewsItem as NewsItemRow

logger = get_logger("ingest_rss")

RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "theblock": "https://www.theblock.co/rss.xml",
}

# DECISION: Binance no publica un RSS público estable para anuncios
# (listados/delistados, sección 12.2). Se usa el endpoint JSON que
# consume su propia web de anuncios — no documentado oficialmente,
# verificado manualmente (devuelve artículos reales). Riesgo explícito:
# puede cambiar de forma sin aviso. Fail-closed por fuente: si la
# respuesta no tiene la forma esperada, se loguea y se salta ESTA fuente
# sin tumbar el resto de `ingest_all`.
BINANCE_ANNOUNCEMENTS_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
BINANCE_ANNOUNCEMENTS_CATALOG_ID = 48  # catálogo general de anuncios (incluye listados/delistados)

# Ticker <-> alias comunes para el universo del MVP (UNIVERSE en
# app/config.py). Heurística determinista para filtrar por activo — NO es
# la clasificación (stance/event_types), eso lo hace el clasificador
# Ollama (fuera de este arranque, ver docs/PHASE_2_REPORT.md).
ASSET_ALIASES: dict[str, list[str]] = {
    "BTC": ["btc", "bitcoin"],
    "ETH": ["eth", "ethereum", "ether"],
    "SOL": ["sol", "solana"],
    "BNB": ["bnb", "binance coin"],
    "XRP": ["xrp", "ripple"],
    "ADA": ["ada", "cardano"],
    "DOGE": ["doge", "dogecoin"],
    "AVAX": ["avax", "avalanche"],
    "LINK": ["link", "chainlink"],
    "DOT": ["dot", "polkadot"],
}


def content_hash(source: str, external_id: str) -> str:
    return hashlib.sha256(f"{source}:{external_id}".encode()).hexdigest()


def extract_asset_tags(text: str, universe_bases: list[str] | None = None) -> list[str]:
    """Palabra completa, case-insensitive, contra los alias conocidos de
    cada ticker. `universe_bases` filtra a un subconjunto de
    `ASSET_ALIASES`; por defecto usa todos los conocidos."""
    bases = universe_bases if universe_bases is not None else list(ASSET_ALIASES.keys())
    text_lower = text.lower()
    tags = []
    for base in bases:
        aliases = ASSET_ALIASES.get(base.upper(), [])
        if any(re.search(rf"\b{re.escape(alias)}\b", text_lower) for alias in aliases):
            tags.append(base.upper())
    return tags


def parse_rss_feed(source: str, raw_bytes: bytes, now: datetime) -> list[NewsItem]:
    parsed = feedparser.parse(raw_bytes)
    items = []
    for entry in parsed.entries:
        title = str(entry.get("title", "")).strip()
        if not title:
            continue
        link = entry.get("link")
        external_id = str(entry.get("id") or link or title)
        body = entry.get("summary") or entry.get("description")
        published_at = None
        published_parsed = entry.get("published_parsed")
        if published_parsed:
            year, month, day, hour, minute, second = published_parsed[:6]
            published_at = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
        items.append(
            NewsItem(
                source=source,
                source_url=link,
                title=title,
                body_text=body,
                asset_tags=extract_asset_tags(f"{title} {body or ''}"),
                published_at=published_at,
                fetched_at=now,
                content_hash=content_hash(source, external_id),
                raw={"title": title, "link": link, "id": entry.get("id")},
            )
        )
    return items


def parse_binance_announcements(raw: dict[str, Any], now: datetime) -> list[NewsItem]:
    articles = raw.get("data", {}).get("articles", [])
    items = []
    for article in articles:
        title = str(article.get("title") or "").strip()
        code = article.get("code")
        if not title or not code:
            continue
        items.append(
            NewsItem(
                source="binance_announcements",
                source_url=None,
                title=title,
                body_text=None,
                asset_tags=extract_asset_tags(title),
                published_at=None,
                fetched_at=now,
                content_hash=content_hash("binance_announcements", str(code)),
                raw={"id": article.get("id"), "code": code, "title": title},
            )
        )
    return items


async def fetch_rss_feed(source: str, url: str, now: datetime) -> list[NewsItem]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    return parse_rss_feed(source, response.content, now)


async def fetch_binance_announcements(now: datetime) -> list[NewsItem]:
    params = {"catalogId": BINANCE_ANNOUNCEMENTS_CATALOG_ID, "pageNo": 1, "pageSize": 20}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(BINANCE_ANNOUNCEMENTS_URL, params=params)
        response.raise_for_status()
    return parse_binance_announcements(response.json(), now)


async def persist_news_items(session: AsyncSession, items: list[NewsItem]) -> int:
    if not items:
        return 0
    rows = [
        {
            "source": item.source,
            "source_url": item.source_url,
            "title": item.title,
            "body_text": item.body_text,
            "asset_tags": item.asset_tags,
            "published_at": item.published_at,
            "fetched_at": item.fetched_at,
            "content_hash": item.content_hash,
            "raw_jsonb": item.raw,
        }
        for item in items
    ]
    stmt = pg_insert(NewsItemRow).values(rows).on_conflict_do_nothing(index_elements=[NewsItemRow.content_hash])
    result = await session.execute(stmt)
    return cast("CursorResult[Any]", result).rowcount or 0


async def ingest_all(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Corre las 3 fuentes de forma independiente — un fallo en una NO debe
    tumbar las otras (fail-closed por fuente, no global, sección 12.2)."""
    now = now or datetime.now(tz=UTC)
    counts: dict[str, int] = {}

    for source, url in RSS_FEEDS.items():
        try:
            items = await fetch_rss_feed(source, url, now)
            counts[source] = await persist_news_items(session, items)
        except Exception:
            logger.exception("ingest.source_failed", source=source)
            counts[source] = 0

    try:
        items = await fetch_binance_announcements(now)
        counts["binance_announcements"] = await persist_news_items(session, items)
    except Exception:
        logger.exception("ingest.source_failed", source="binance_announcements")
        counts["binance_announcements"] = 0

    return counts
