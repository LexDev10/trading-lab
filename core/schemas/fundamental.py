"""Contrato del almacén PIT (sección 12.1, fase 2). Solo `NewsItem` por
ahora — `SocialItem`/`ItemClassification` se añaden cuando se construya
Reddit/el clasificador (fuera de este arranque de fase, ver
`services/fundamental/ingest_rss.py`)."""

from typing import Any

from pydantic import AwareDatetime, BaseModel


class NewsItem(BaseModel):
    source: str
    source_url: str | None
    title: str
    body_text: str | None
    asset_tags: list[str]
    published_at: AwareDatetime | None
    fetched_at: AwareDatetime
    content_hash: str
    raw: dict[str, Any]
