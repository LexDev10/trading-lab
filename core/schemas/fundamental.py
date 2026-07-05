"""Contrato del almacén PIT (sección 12.1, fase 2). `ItemClassification`
se añade cuando se construya el clasificador Ollama (todavía fuera de
alcance, ver `docs/PHASE_2_REPORT.md`)."""

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


class SocialItem(BaseModel):
    platform: str
    subreddit: str
    post_id: str
    title: str
    body_text: str | None
    score_at_fetch: int
    num_comments_at_fetch: int
    published_at: AwareDatetime | None
    fetched_at: AwareDatetime
    raw: dict[str, Any]
