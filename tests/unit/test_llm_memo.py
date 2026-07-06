"""Memo LLM opcional (sección 13) — sin red real: `httpx.AsyncClient`
monkeypatcheado, mismo patrón que `test_telegram.py`. No-op sin
flag/credenciales, arma bien el payload de Anthropic con credenciales,
fail-open ante un error de red."""

from app.config import Settings
from services.reporting import llm_memo


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
    def __init__(self, calls: list, *, raise_error: bool = False, payload: dict | None = None) -> None:
        self._calls = calls
        self._raise_error = raise_error
        self._payload = payload or {"content": [{"text": "Memo generado."}]}

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict, headers: dict) -> _FakeResponse:
        self._calls.append((url, json, headers))
        if self._raise_error:
            raise RuntimeError("network down")
        return _FakeResponse(self._payload)


async def test_generate_trade_memo_noop_without_flag(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_memo.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls))
    settings = base_settings(use_remote_llm=False, remote_llm_api_key="a-key")

    memo = await llm_memo.generate_trade_memo(settings, {"asset": "BTCUSDT"})

    assert memo is None
    assert calls == []


async def test_generate_trade_memo_noop_without_api_key(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_memo.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls))
    settings = base_settings(use_remote_llm=True, remote_llm_api_key="")

    memo = await llm_memo.generate_trade_memo(settings, {"asset": "BTCUSDT"})

    assert memo is None
    assert calls == []


async def test_generate_trade_memo_calls_anthropic_with_credentials(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_memo.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls))
    settings = base_settings(use_remote_llm=True, remote_llm_api_key="sk-test", remote_llm_model="claude-haiku-4-5-20251001")

    memo = await llm_memo.generate_trade_memo(settings, {"asset": "BTCUSDT", "decision": {"policy_action": "enter"}})

    assert memo == "Memo generado."
    assert len(calls) == 1
    url, payload, headers = calls[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert payload["model"] == "claude-haiku-4-5-20251001"
    assert "BTCUSDT" in payload["messages"][0]["content"]
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"


async def test_generate_trade_memo_fails_open_on_network_error(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm_memo.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, raise_error=True))
    settings = base_settings(use_remote_llm=True, remote_llm_api_key="sk-test")

    memo = await llm_memo.generate_trade_memo(settings, {"asset": "BTCUSDT"})  # no debe lanzar

    assert memo is None
