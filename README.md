# trading-lab

Sistema multiagente de trading (crypto spot, swing corto). La especificación
completa del sistema, principios de diseño y fases está en
[ESPECIFICACION_SISTEMA_TRADING.md](ESPECIFICACION_SISTEMA_TRADING.md) —
léela antes de tocar código.

Estado actual: **Fase 0 completa**, **Fase 1 en progreso** (scanner
automático + pipeline técnico + risk engine + `/analiza` + backtesting
walk-forward funcionando contra datos reales; falta executor OCO, monitor
de posiciones, paper ledger, reconciliación y Telegram — bloqueado por
credenciales de testnet/Telegram que el proyecto aún no tiene). Ver
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
  scheduler (APScheduler in-process). Cada `SCAN_INTERVAL_MINUTES`
  (default 15 min) corre un ciclo que: (1) ingesta velas 1h/4h y ticker
  24h del universo, (2) escanea todo `UNIVERSE` (régimen BTC + filtros
  duros + técnico + risk engine) y registra un `decision_log`
  (`trigger=scheduled`) por cada activo, entre o no entre.

Verificar:

```bash
curl http://localhost:8000/health
```

Debe responder `db_ok: true` y, tras el primer ciclo del scheduler,
`data_fresh: true`.

## Análisis manual (`/analiza`)

Equivalente CLI de la sección 21.2 — corre el mismo pipeline compartido
(`services/scanner/scanner.py`) que el ciclo automático, para un par
concreto:

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

## Halt / rearme manual del killswitch

```bash
docker compose exec app uv run python -m scripts.halt "motivo"
docker compose exec app uv run python -m scripts.rearm
```

Con el sistema en `halt`, el risk engine rechaza toda entrada nueva
(`checks["system_not_halted"]=False`) hasta el rearme explícito — nunca
automático (sección 9.2).

## Backtesting (walk-forward)

```bash
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
```

Resultados y metodología completos en [backtests/RESULTS.md](backtests/RESULTS.md).

## Tests

```bash
# Unitarios (sin DB, rápidos)
docker compose run --rm --no-deps app uv run pytest -v

# Integración (necesitan Postgres real levantado)
docker compose exec app uv run pytest tests/integration -v

# Tipado estricto
docker compose run --rm --no-deps app uv run mypy   # core/ y services/risk/
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

- No hay executor (`binance_executor.py`, OCO en testnet), monitor de
  posiciones, paper ledger ni reconciliación — nada se ejecuta de verdad
  todavía, ni en modo "operar" (el informe lo dice explícitamente cuando
  el risk engine habría aprobado). **Bloqueado por credenciales**:
  necesita `BINANCE_API_KEY`/`SECRET` de testnet.
- No hay Telegram configurado (`/estado`, alertas). **Bloqueado por
  credenciales**: necesita `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
- No hay claves de Binance configuradas; no hacen falta para lo que existe
  hoy (los datos usados son endpoints públicos de solo lectura).
- `equity_snapshots` está vacío hasta que exista el paper ledger; el risk
  engine usa `PAPER_STARTING_EQUITY_USDT` como arranque (ver Apéndice A).
- El backtest testea cada timeframe (1h/4h) de forma independiente y
  compone los trades out-of-sample de forma secuencial (no simula cartera
  multi-posición real) — limitaciones completas en `backtests/RESULTS.md`.
