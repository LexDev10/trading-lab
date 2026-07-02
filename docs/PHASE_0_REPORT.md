# Fase 0 — Infraestructura mínima (sin trading)

Estado: **completa**. Verificado end-to-end contra Docker real y la API
pública de Binance (producción, solo lectura) en esta máquina.

## Qué se hizo

- Repo scaffolding: `pyproject.toml` (uv), `Dockerfile` multi-stage con uv,
  `docker-compose.yml` (`postgres` + `app`), `.env.example`, `.gitignore`,
  `.dockerignore`.
- `app/config.py`: `Settings` (Pydantic Settings) con **todos** los
  parámetros del Apéndice A del documento de especificación y sus defaults.
- `core/logging.py`: logging JSON estructurado a stdout vía `structlog`.
- `core/schemas/market.py`: contratos Pydantic `Candle` y `MarketSnapshot`
  (todo `Decimal`, nunca `float`, en precios/cantidades/volúmenes).
- `db/models.py` + Alembic (`db/migrations/`): tablas `assets`, `candles`
  (PK compuesta `asset, timeframe, open_time`) y `market_snapshots`, tal
  como se definen en la sección 16 del documento.
- `services/data/binance_market_data.py`: cliente de solo lectura contra
  `https://api.binance.com` (producción) para klines 1h/4h y ticker 24h.
  **Decisión de diseño explícita**: los datos de mercado siempre vienen de
  producción, independientemente de `ENVIRONMENT`, según sección 10.1 —
  solo la ejecución de órdenes (fase 1+) usará la URL de testnet.
- `services/data/persistence.py`: upsert idempotente (`ON CONFLICT DO
  UPDATE`) de assets y velas; insert append-only de snapshots.
- `app/scheduler.py`: APScheduler in-process (sin Celery/n8n, según
  sección 5), un job `ingest_market_data_job` cada `SCAN_INTERVAL_MINUTES`
  (default 15 min) para los 10 pares del universo por defecto.
- `app/main.py`: FastAPI con lifespan que arranca/para el scheduler, y
  `/health` que reporta estado de DB, frescura de datos (< 2h), modo,
  environment y `git_sha`.

## Qué lo cubre

- `tests/unit/test_config.py`: defaults de `Settings` coinciden con el
  Apéndice A; parsing de listas (`UNIVERSE`, `CORE_ASSETS`).
- `tests/unit/test_market_schema.py`: `Candle`/`MarketSnapshot` usan
  `Decimal`, no `float`.
- `tests/unit/test_binance_market_data.py`: parseo de respuestas reales de
  Binance (fixtures grabadas en `tests/fixtures/`) a los contratos Pydantic,
  incluido el cálculo de `spread_bps`.
- **Verificación manual end-to-end** (no automatizada todavía, se añadirá
  como test de integración en fase 1): `docker compose up -d --build`
  levantó postgres + app, Alembic aplicó la migración inicial, el scheduler
  ejecutó el job de ingesta contra la API real de Binance y persistió
  10 assets, 5000 velas 1h + 5000 velas 4h (500 por par × 10 pares) y 10
  snapshots de ticker 24h. `/health` respondió `db_ok: true`,
  `data_fresh: true`.

## Bug encontrado y corregido durante la verificación

Las columnas `open_time` / `ts` en `db/models.py` no declaraban
`DateTime(timezone=True)` explícito (SQLAlchemy infiere `DateTime()` naive
por defecto desde `Mapped[datetime]`), aunque la migración de Alembic sí
creaba las columnas como `timestamptz`. Esto rompía la ingesta real con
`asyncpg.exceptions.DataError: can't subtract offset-naive and
offset-aware datetimes` porque los `Candle`/`MarketSnapshot` parseados
llevan datetimes con tzinfo (UTC). Corregido añadiendo `TIMESTAMPTZ =
DateTime(timezone=True)` explícito en ambas columnas. Este tipo de
discrepancia silenciosa entre el tipo Python inferido y el tipo real de
columna es exactamente la clase de bug que los tests unitarios con
fixtures no detectan (no tocan Postgres) — motiva añadir un test de
integración contra Postgres real en fase 1.

## Qué quedó fuera (deliberadamente, corresponde a fases posteriores)

- Scanner, filtros duros, filtro de régimen BTC (fase 1).
- Agente técnico, indicadores, risk engine, executor, monitor, backtesting
  (fase 1).
- `core/enums.py` y el resto de `core/schemas/` (technical, fundamental,
  decision, risk, orders) — se definen cuando su fase los necesita, para no
  adelantar contratos de features no implementadas todavía.
- Telegram, capa fundamental, meta-decider, dashboard, gates de capital
  real — fases 1 a 4.
- `mypy --strict`: la configuración está en `pyproject.toml` pero no se
  corre todavía en CI porque `services/risk/` no existe aún.

## Cómo reproducir la verificación

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
docker compose run --rm --no-deps app uv run pytest -v
docker compose down
```
