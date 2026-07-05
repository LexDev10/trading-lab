# trading-lab

Sistema multiagente de trading (crypto spot, swing corto). La especificación
completa del sistema, principios de diseño y fases está en
[ESPECIFICACION_SISTEMA_TRADING.md](ESPECIFICACION_SISTEMA_TRADING.md) —
léela antes de tocar código.

Estado actual: **Fase 0 completa**, **Fase 1 pausada en progreso** (todo
listo salvo el executor OCO real contra testnet, bloqueado por
credenciales — scanner automático, risk engine, `/analiza`, `/estado`,
backtesting walk-forward, paper ledger interno y **alertas de Telegram**
ya funcionan), **Fase 2 en arranque** (almacén PIT + ingesta RSS/JSON
funcionando; clasificador Ollama y Reddit quedan para una siguiente
iteración). Ver [docs/PHASE_0_REPORT.md](docs/PHASE_0_REPORT.md),
[docs/PHASE_1_REPORT.md](docs/PHASE_1_REPORT.md) y
[docs/PHASE_2_REPORT.md](docs/PHASE_2_REPORT.md).

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

# Modo operar (respeta el risk engine; si aprueba, abre una posición de
# PAPEL — simulación sobre velas reales, sin exchange — nunca una orden
# real; ver "Paper trading" más abajo)
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
```

Funciona también con pares fuera del `UNIVERSE` configurado (los descarga
on-demand, sección 21.3) y rechaza stablecoins con un mensaje explicativo
(sección 21.5).

## Paper trading (sin credenciales)

Cuando el risk engine aprueba una entrada (ciclo automático o `/analiza
... operar`), el sistema abre una posición de **papel**: simulación sobre
velas reales ya ingeridas, sin llamar a ningún exchange
(`services/execution/paper_ledger.py`). Se sigue vela a vela hasta SL, TP,
invalidación técnica o expiración por horizonte, con fee simulada de
0.1%/lado — sustituto temporal del executor OCO real contra testnet
mientras no existan credenciales (`# DECISION`, sección 10.1 del
documento).

```bash
docker compose exec app uv run python -m scripts.estado
```

Muestra sistema, régimen BTC, equity y drawdown actuales, posiciones de
papel abiertas y un resumen de rentabilidad de las cerradas (win rate,
pnl total, pnl% medio, profit factor) — equivalente CLI de `/estado`
(sección 21, hoy solo speced para Telegram).

## Alertas de Telegram

Configura `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` en `.env` (crea el bot
con [@BotFather](https://t.me/BotFather); el chat_id se obtiene escribiéndole
al bot y consultando `https://api.telegram.org/bot<TOKEN>/getUpdates`).
Solo alertas salientes (sección 17), sin comandos interactivos — `/analiza`
y `/estado` ya existen como CLI, ver arriba:
- Nueva posición de papel / cierre con PnL (`services/execution/paper_ledger.py`).
- Halt / rearme del killswitch (`scripts/halt.py` / `scripts/rearm.py`).
- Resumen diario a las 22:00 UTC (`services/reporting/daily_summary.py`):
  equity, drawdown, trades del día, rechazos por motivo y estado de
  `CORE_ASSETS` aunque no haya habido setup (sección 21.4).

Sin credenciales configuradas, `notifications/telegram.py` no hace nada
(no falla) — el sistema funciona igual, solo sin alertas. Un fallo de red
al enviar tampoco bloquea nada (fail-open, a diferencia del resto del
sistema): un aviso perdido no puede tumbar un ciclo del scheduler.

## Capa fundamental (fase 2, arranque)

Cada `SCAN_INTERVAL_MINUTES` se ingesta al almacén PIT inmutable
(`news_items`, sección 12.1) desde anuncios de Binance, CoinDesk y The
Block (`services/fundamental/ingest_rss.py`), sin credenciales. Cada
fuente falla de forma independiente (un feed caído no tumba las otras).
Todavía **no** hay clasificación (Ollama) ni veto fundamental — ver
limitaciones y `docs/PHASE_2_REPORT.md`.

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
docker compose run --rm --no-deps app uv run mypy
# core/, services/risk/, services/execution/, services/fundamental/,
# services/reporting/, notifications/
```

## Configuración

Todos los parámetros del sistema (universo, filtros, risk engine, etc.) se
leen de variables de entorno — ver `.env.example` y `app/config.py`. Nunca
hardcodear valores en el código.

## Dependencias añadidas fuera de la sección 5 del documento

Sección 20 regla 6: justificación de lo no listado en el stack original.
- `httpx`: cliente HTTP async para la API de Telegram y la ingesta
  RSS/JSON de fase 2 — encaja con el resto del stack (todo async).
- `feedparser`: parseo de RSS estándar (CoinDesk, The Block) — librería
  estándar de facto para RSS en Python, sin dependencias pesadas.

## Migraciones

Con el stack levantado:

```bash
docker compose exec app uv run alembic revision --autogenerate -m "mensaje"
docker compose exec app uv run alembic upgrade head
```

## Limitaciones conocidas (estado actual)

- No hay executor real (`binance_executor.py`, OCO en testnet) ni
  reconciliación — nada se envía de verdad a ningún exchange todavía, ni
  en modo "operar" (lo que hay es el paper ledger interno, ver arriba).
  **Bloqueado por credenciales**: necesita `BINANCE_API_KEY`/`SECRET` de
  testnet.
- El paper ledger es una simplificación explícita: sin redondeo a filtros
  reales de exchange (tickSize/stepSize/minNotional), sin veto
  fundamental (fase 2+), y el seguimiento de posiciones corre a la
  cadencia de 15 min del ciclo existente en vez de los 5 min de la
  sección 11 (pensados para el monitor real). Detalle completo en
  `services/execution/paper_ledger.py`.
- No hay claves de Binance configuradas; no hacen falta para lo que existe
  hoy (los datos usados son endpoints públicos de solo lectura).
- `equity_snapshots` empieza vacío en cada entorno nuevo; el risk engine
  usa `PAPER_STARTING_EQUITY_USDT` como arranque hasta que el paper ledger
  cierre la primera posición (ver Apéndice A).
- El backtest testea cada timeframe (1h/4h) de forma independiente y
  compone los trades out-of-sample de forma secuencial (no simula cartera
  multi-posición real) — limitaciones completas en `backtests/RESULTS.md`.
- Fase 2 (capa fundamental) está solo arrancada: hay almacén PIT + ingesta
  RSS/JSON, pero no hay Reddit (necesita registrar una app OAuth,
  credenciales que el proyecto aún no tiene), ni clasificador Ollama
  (necesita decidir conectividad: servicio en docker-compose vs Ollama del
  host), ni `classifier_scorecard`, ni veto fundamental — el sistema sigue
  operando 100% técnico (`MODE=technical_only`). El endpoint de anuncios
  de Binance usado no es RSS oficial ni documentado (ver `# DECISION` en
  la sección 12.2 del documento) — puede romperse sin aviso; está aislado
  por fuente (fail-closed local, no tumba las otras dos).
