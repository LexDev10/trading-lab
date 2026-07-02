# trading-lab

Sistema multiagente de trading (crypto spot, swing corto). La especificación
completa del sistema, principios de diseño y fases está en
[ESPECIFICACION_SISTEMA_TRADING.md](ESPECIFICACION_SISTEMA_TRADING.md) —
léela antes de tocar código.

Estado actual: **Fase 0 completa**, **Fase 1 en progreso** (pipeline técnico
+ risk engine + `/analiza` funcionando contra datos reales; falta executor
OCO, monitor de posiciones, backtesting y Telegram). Ver
[docs/PHASE_0_REPORT.md](docs/PHASE_0_REPORT.md) y
[docs/PHASE_1_REPORT.md](docs/PHASE_1_REPORT.md).

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

## Análisis manual (`/analiza`)

Equivalente CLI de la sección 21.2 — corre el pipeline completo (scanner +
técnico + risk engine) para un par, con el stack levantado:

```bash
# Modo informe (default, NUNCA ejecuta)
docker compose exec app uv run python -m scripts.analiza SOLUSDT

# Modo operar (respeta el risk engine; en este build no envía órdenes
# reales porque el executor de fase 1 todavía no está implementado)
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
```

Funciona también con pares fuera del `UNIVERSE` configurado (los descarga
on-demand, sección 21.3) y rechaza stablecoins con un mensaje explicativo
(sección 21.5).

## Tests

```bash
docker compose run --rm --no-deps app uv run pytest -v
docker compose run --rm --no-deps app uv run mypy   # core/ y services/risk/, estricto
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

## Limitaciones conocidas (estado actual)

- El scanner automático (ciclo cada `SCAN_INTERVAL_MINUTES` sobre todo el
  `UNIVERSE`) todavía no está enganchado al scheduler — hoy solo se ejecuta
  el pipeline técnico+risk vía `/analiza` (manual). El job en background
  sigue siendo solo ingesta de mercado (Fase 0).
- No hay executor (`binance_executor.py`, OCO en testnet), monitor de
  posiciones ni backtesting todavía — nada se ejecuta de verdad, ni en
  modo "operar".
- No hay Telegram configurado; no hace falta todavía.
- No hay claves de Binance configuradas; no hacen falta para lo que existe
  hoy (los datos usados son endpoints públicos de solo lectura).
- `equity_snapshots` está vacío hasta que exista el paper ledger; el risk
  engine usa `PAPER_STARTING_EQUITY_USDT` como arranque (ver Apéndice A).
