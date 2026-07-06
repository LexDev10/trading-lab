"""Clasificador Ollama (sección 12.3) — sin red real: `httpx.AsyncClient`
monkeypatcheado, mismo patrón que `test_telegram.py`. Cubre la validación
estricta del lado Python (fail-closed ante un valor fuera de enum, ver
`# DECISION` en `services/fundamental/classify.py`) y la resolución de
`asset_tags` por tipo de item."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from db.models import NewsItem as NewsItemRow
from db.models import SocialItem as SocialItemRow
from services.fundamental import classify


def base_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, calls: list, *, chat_payload: dict | None = None, tags_payload: dict | None = None) -> None:
        self._calls = calls
        self._chat_payload = chat_payload
        self._tags_payload = tags_payload

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict) -> _FakeResponse:
        self._calls.append(("POST", url, json))
        assert self._chat_payload is not None
        return _FakeResponse(self._chat_payload)

    async def get(self, url: str) -> _FakeResponse:
        self._calls.append(("GET", url, None))
        assert self._tags_payload is not None
        return _FakeResponse(self._tags_payload)


def test_parse_classification_valid():
    raw = {
        "stance": "bearish_strong",
        "event_types": ["hack_exploit"],
        "veto": True,
        "summary": "Exploit grande en un puente.",
    }
    parsed = classify.ParsedClassification.model_validate(raw)
    assert parsed.stance.value == "bearish_strong"
    assert parsed.event_types[0].value == "hack_exploit"
    assert parsed.veto is True


def test_parse_classification_invalid_stance_raises():
    raw = {"stance": "muy_alcista", "event_types": [], "veto": False, "summary": "x"}
    with pytest.raises(ValidationError):
        classify.ParsedClassification.model_validate(raw)


def test_parse_classification_invalid_event_type_raises():
    raw = {"stance": "neutral", "event_types": ["invasion_marciana"], "veto": False, "summary": "x"}
    with pytest.raises(ValidationError):
        classify.ParsedClassification.model_validate(raw)


def test_resolve_asset_tags_news_reuses_stored_tags():
    news_row = NewsItemRow(asset_tags=["BTC", "ETH"])
    assert classify.resolve_asset_tags("news", news_row=news_row) == ["BTC", "ETH"]


def test_resolve_asset_tags_social_known_subreddit():
    social_row = SocialItemRow(subreddit="Bitcoin")
    assert classify.resolve_asset_tags("social", social_row=social_row) == ["BTC"]


def test_resolve_asset_tags_social_generic_subreddit_has_no_asset():
    social_row = SocialItemRow(subreddit="CryptoCurrency")
    assert classify.resolve_asset_tags("social", social_row=social_row) == []


async def test_call_ollama_sends_loose_json_format_and_temperature_zero(monkeypatch):
    calls: list = []
    chat_payload = {"message": {"content": '{"stance": "neutral", "event_types": [], "veto": false, "summary": "ok"}'}}
    monkeypatch.setattr(
        classify.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, chat_payload=chat_payload)
    )
    settings = base_settings(ollama_host="http://ollama.test:11434", ollama_model="test-model")

    result = await classify.call_ollama(settings, "un prompt")

    assert result == {"stance": "neutral", "event_types": [], "veto": False, "summary": "ok"}
    assert len(calls) == 1
    method, url, payload = calls[0]
    assert method == "POST"
    assert url == "http://ollama.test:11434/api/chat"
    assert payload["model"] == "test-model"
    assert payload["format"] == "json"
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0}


async def test_get_model_version_found(monkeypatch):
    calls: list = []
    tags_payload = {"models": [{"name": "other-model", "digest": "aaa"}, {"name": "test-model", "digest": "abcdef012345678"}]}
    monkeypatch.setattr(
        classify.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, tags_payload=tags_payload)
    )
    settings = base_settings(ollama_model="test-model")

    version = await classify.get_model_version(settings)

    assert version == "abcdef012345"


async def test_get_model_version_not_downloaded_returns_none(monkeypatch):
    calls: list = []
    tags_payload = {"models": [{"name": "other-model", "digest": "aaa"}]}
    monkeypatch.setattr(
        classify.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, tags_payload=tags_payload)
    )
    settings = base_settings(ollama_model="test-model")

    assert await classify.get_model_version(settings) is None
