"""Ingesta Reddit OAuth Data API (sección 12.2) al almacén PIT
(`social_items`): r/CryptoCurrency + el subreddit de cada activo del
universo. Mismo patrón que `services/fundamental/ingest_rss.py`:
separación red/parseo (testeable con fixtures sin red), fail-closed por
subreddit (uno caído no tumba los demás).

# DECISION: grant `client_credentials` (autenticación "app-only", sin
# usuario/contraseña de Reddit) — solo necesitamos lectura pública de
# listados de subreddits, ninguna acción en nombre de una cuenta. Evita
# guardar credenciales de una cuenta personal de Reddit; a cambio, el
# token tiene límites de acceso algo más estrictos que el grant
# `password`, pero de sobra para leer `new`/`top` de un subreddit público.
#
# Sin REDDIT_CLIENT_ID/SECRET configurados, `ingest_all` no hace nada (no
# falla) — mismo criterio que `notifications/telegram.py`.
"""

from datetime import UTC, datetime
from typing import Any, cast

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from core.logging import get_logger
from core.schemas.fundamental import SocialItem
from db.models import SocialItem as SocialItemRow

logger = get_logger("ingest_reddit")

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
LISTING_URL = "https://oauth.reddit.com/r/{subreddit}/new"
LISTING_LIMIT = 25

CORE_SUBREDDIT = "CryptoCurrency"

# DECISION: subreddit "del activo" para cada par del universo del MVP —
# la sección 12.2 no los nombra explícitamente. Mejor esfuerzo: si un
# nombre queda desactualizado o el subreddit cambia, esa fuente
# simplemente no aporta items (fail-closed por subreddit en `ingest_all`,
# no rompe nada). Fácil de corregir sin tocar el resto del módulo.
ASSET_SUBREDDITS: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binance",
    "XRP": "XRP",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "Avax",
    "LINK": "Chainlink",
    "DOT": "Polkadot",
}


async def _get_access_token(settings: Settings) -> str:
    headers = {"User-Agent": settings.reddit_user_agent}
    data = {"grant_type": "client_credentials"}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            TOKEN_URL,
            auth=(settings.reddit_client_id, settings.reddit_client_secret),
            headers=headers,
            data=data,
        )
        response.raise_for_status()
    return str(response.json()["access_token"])


def parse_reddit_listing(subreddit: str, raw: dict[str, Any], now: datetime) -> list[SocialItem]:
    children = raw.get("data", {}).get("children", [])
    items = []
    for child in children:
        post = child.get("data", {})
        post_id = post.get("id")
        title = str(post.get("title") or "").strip()
        if not post_id or not title:
            continue
        created_utc = post.get("created_utc")
        published_at = datetime.fromtimestamp(created_utc, tz=UTC) if created_utc else None
        items.append(
            SocialItem(
                platform="reddit",
                subreddit=subreddit,
                post_id=str(post_id),
                title=title,
                body_text=post.get("selftext") or None,
                score_at_fetch=int(post.get("score") or 0),
                num_comments_at_fetch=int(post.get("num_comments") or 0),
                published_at=published_at,
                fetched_at=now,
                raw={"id": post_id, "permalink": post.get("permalink")},
            )
        )
    return items


async def fetch_subreddit_posts(subreddit: str, token: str, settings: Settings, now: datetime) -> list[SocialItem]:
    headers = {"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent}
    url = LISTING_URL.format(subreddit=subreddit)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, headers=headers, params={"limit": LISTING_LIMIT})
        response.raise_for_status()
    return parse_reddit_listing(subreddit, response.json(), now)


async def persist_social_items(session: AsyncSession, items: list[SocialItem]) -> int:
    if not items:
        return 0
    rows = [
        {
            "platform": item.platform,
            "subreddit": item.subreddit,
            "post_id": item.post_id,
            "title": item.title,
            "body_text": item.body_text,
            "score_at_fetch": item.score_at_fetch,
            "num_comments_at_fetch": item.num_comments_at_fetch,
            "published_at": item.published_at,
            "fetched_at": item.fetched_at,
            "raw_jsonb": item.raw,
        }
        for item in items
    ]
    stmt = pg_insert(SocialItemRow).values(rows).on_conflict_do_nothing(index_elements=[SocialItemRow.post_id])
    result = await session.execute(stmt)
    return cast("CursorResult[Any]", result).rowcount or 0


def _subreddits_for_universe(settings: Settings) -> list[str]:
    bases = [asset.removesuffix("USDT") for asset in settings.universe_list]
    asset_subreddits = [ASSET_SUBREDDITS[base] for base in bases if base in ASSET_SUBREDDITS]
    return [CORE_SUBREDDIT, *asset_subreddits]


async def ingest_all(session: AsyncSession, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Corre un subreddit a la vez, fail-closed por subreddit (sección
    12.2, mismo criterio que `ingest_rss.ingest_all`). Sin credenciales,
    no hace nada — no es un error, es un requisito externo pendiente."""
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        return {}

    now = now or datetime.now(tz=UTC)
    try:
        token = await _get_access_token(settings)
    except Exception:
        logger.exception("ingest.token_failed")
        return {}

    counts: dict[str, int] = {}
    for subreddit in _subreddits_for_universe(settings):
        try:
            items = await fetch_subreddit_posts(subreddit, token, settings, now)
            counts[f"reddit_{subreddit}"] = await persist_social_items(session, items)
        except Exception:
            logger.exception("ingest.subreddit_failed", subreddit=subreddit)
            counts[f"reddit_{subreddit}"] = 0

    return counts
