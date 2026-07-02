# trading-lab

Sistema multiagente de trading (crypto spot, swing corto). La especificación
completa del sistema, principios de diseño y fases está en
[ESPECIFICACION_SISTEMA_TRADING.md](ESPECIFICACION_SISTEMA_TRADING.md) —
léela antes de tocar código.

Estado actual: **Fase 0 completa** (infraestructura mínima, sin trading).
Ver [docs/PHASE_0_REPORT.md](docs/PHASE_0_REPORT.md).

## Requisitos

- Docker Desktop (con Docker Compose v2+)
- Nada más en el host: todo corre en contenedores.

## Arranque rápido

```bash
cp .env.example .env
docker compose up -d --build
```

Esto levanta:
- `postgres`: PostgreSQL 16.
- `app`: aplica migraciones de Alembic, arranca FastAPI (`/health`) y el
  scheduler (APScheduler in-process) que ingesta velas 1h/4h y ticker 24h
  del universo configurado cada `SCAN_INTERVAL_MINUTES` (default 15 min).

Verificar:

```bash
curl http://localhost:8000/health
```

Debe responder `db_ok: true` y, tras el primer ciclo del scheduler,
`data_fresh: true`.

## Tests

```bash
docker compose run --rm --no-deps app uv run pytest -v
```

## Configuración

Todos los parámetros del sistema (universo, filtros, risk engine, etc.) se
leen de variables de entorno — ver `.env.example` y `app/config.py`. Nunca
hardcodear valores en el código.

## Migraciones

Con el stack levantado:

```bash
docker compose exec app uv run alembic revision --autogenerate -m "mensaje"
docker compose exec app uv run alembic upgrade head
```

## Limitaciones conocidas de la Fase 0

- No hay scanner, técnica, risk engine ni ejecución todavía (llegan en Fase 1).
- No hay claves de Binance ni Telegram configuradas; no hacen falta para
  Fase 0 (los datos de mercado usados son endpoints públicos de solo lectura).
- `mypy --strict` sobre `core/` y `services/risk/` se activa formalmente
  cuando exista `services/risk/` (Fase 1).
