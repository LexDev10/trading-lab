"""`notifications/telegram.py` — sin red real: `httpx.AsyncClient`
monkeypatcheado. Confirma que no envía nada sin credenciales, que arma
bien la petición cuando sí las hay, y que es fail-open (una excepción de
red no se propaga — sección 17, un fallo de alertas no puede tumbar un
ciclo del scheduler)."""

from app.config import Settings
from notifications import telegram


def base_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


class _FakeAsyncClient:
    def __init__(self, calls: list, *, raise_error: bool = False, **_kwargs) -> None:
        self._calls = calls
        self._raise_error = raise_error

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict) -> _FakeResponse:
        self._calls.append((url, json))
        if self._raise_error:
            raise RuntimeError("network down")
        return _FakeResponse()


async def test_send_message_noop_without_credentials(monkeypatch):
    calls: list = []
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, **kwargs))
    # `_env_file=None` solo desactiva el .env; las env vars reales del
    # proceso (el contenedor ya tiene TELEGRAM_* inyectadas por
    # docker-compose) siguen aplicando, así que hay que forzar vacío a
    # mano para probar el caso "sin credenciales".
    settings = base_settings(telegram_bot_token="", telegram_chat_id="")

    await telegram.send_message(settings, "hola")

    assert calls == []


async def test_send_message_posts_with_credentials(monkeypatch):
    calls: list = []
    monkeypatch.setattr(telegram.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, **kwargs))
    settings = base_settings(telegram_bot_token="TOKEN", telegram_chat_id="123")

    await telegram.send_message(settings, "hola mundo")

    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert payload == {"chat_id": "123", "text": "hola mundo", "parse_mode": "HTML"}


async def test_send_message_fails_open_on_network_error(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        telegram.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(calls, raise_error=True, **kwargs)
    )
    settings = base_settings(telegram_bot_token="TOKEN", telegram_chat_id="123")

    await telegram.send_message(settings, "hola")  # no debe lanzar
