"""Configuración central. Todos los parámetros del documento de especificación
viven aquí con sus defaults; nada se hardcodea en los servicios."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["testnet", "live"] = "testnet"
    mode: Literal["technical_only", "technical_plus_fundamental", "full"] = "technical_only"

    # --- Universo ---
    universe: str = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,DOTUSDT"
    core_assets: str = "BTCUSDT,ETHUSDT"

    # --- Modo manual (/analiza) ---
    manual_max_per_hour: int = 10
    manual_min_candles_1h: int = 500

    # --- Scanner / filtros duros ---
    scan_interval_minutes: int = 15
    min_quote_vol_24h: Decimal = Decimal("50000000")
    max_spread_bps: Decimal = Decimal("5")
    min_abs_change_pct: Decimal = Decimal("3.0")
    range_lookback_candles: int = 20
    volume_confirm_mult: Decimal = Decimal("1.5")
    paper_starting_equity_usdt: Decimal = Decimal("10000")

    # --- Risk engine ---
    risk_per_trade: Decimal = Decimal("0.005")
    max_positions: int = 3
    max_exposure_total: Decimal = Decimal("0.30")
    max_exposure_btc_beta: Decimal = Decimal("0.20")
    daily_loss_limit: Decimal = Decimal("0.02")
    drawdown_killswitch: Decimal = Decimal("0.10")
    cooldown_asset_hours: int = 12
    cooldown_after_2sl_hours: int = 24
    min_rr_net: Decimal = Decimal("1.8")
    taker_fee: Decimal = Decimal("0.001")

    # --- Ejecución ---
    entry_ttl_minutes: int = 45
    max_hold_hours_intraday: int = 48
    max_hold_days_swing: int = 7

    # --- LLM / fundamental (fase 2+) ---
    use_remote_llm: bool = False
    ollama_model: str = "qwen3.5:9b"
    # DECISION (2026-07-03): sin OLLAMA_HOST en el documento original — el
    # contenedor `app` conecta al Ollama que ya corre en el HOST del
    # usuario (decisión explícita del usuario: ya tiene modelos
    # descargados ahí, evita duplicar/redescargar dentro de Docker).
    # `host.docker.internal` funciona out-of-the-box en Docker Desktop
    # (Windows/Mac); en Linux requiere el `extra_hosts` de docker-compose.yml.
    ollama_host: str = "http://host.docker.internal:11434"

    # --- Reddit OAuth Data API (fase 2, ingesta social — sección 12.2) ---
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "trading-lab/0.1 (fundamental ingest)"

    # --- Capital real (fase 4) ---
    live_max_capital: Decimal = Decimal("0")

    # --- Base de datos ---
    database_url: str = "postgresql+asyncpg://trading:trading@localhost:5432/trading_lab"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Binance ---
    binance_api_key: str = ""
    binance_api_secret: str = ""

    @property
    def universe_list(self) -> list[str]:
        return [s.strip() for s in self.universe.split(",") if s.strip()]

    @property
    def core_assets_list(self) -> list[str]:
        return [s.strip() for s in self.core_assets.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
