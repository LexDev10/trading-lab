# trading-lab

Sistema multiagente de trading (crypto spot, swing corto). La especificación
completa del sistema, principios de diseño y fases está en
[ESPECIFICACION_SISTEMA_TRADING.md](ESPECIFICACION_SISTEMA_TRADING.md) —
léela antes de tocar código.

Estado actual: **Fase 0 completa**, **Fase 1 pausada en progreso** (todo
listo salvo el executor OCO real contra testnet, bloqueado por
credenciales — scanner automático, risk engine, `/analiza`, `/estado`,
backtesting walk-forward, paper ledger interno y **alertas de Telegram**
ya funcionan), **Fase 2 casi cerrada** (almacén PIT + ingesta RSS/JSON +
ingesta de Reddit + **clasificador Ollama** (`stance`/`event_types`/
`veto`) + **veto fundamental integrado** (bloquea entradas nuevas y
cierra posiciones abiertas, sección 12.4) + scorecard semanal, todo
funcionando y verificado en vivo contra el Ollama real del usuario —
solo falta rellenar credenciales de Reddit para que esa fuente aporte
datos), **Fase 3 en progreso** (meta-decider por tabla de política +
dashboard `/dashboard` + memo LLM opcional detrás de flag, ver sección
más abajo). Ver [docs/PHASE_0_REPORT.md](docs/PHASE_0_REPORT.md),
[docs/PHASE_1_REPORT.md](docs/PHASE_1_REPORT.md),
[docs/PHASE_2_REPORT.md](docs/PHASE_2_REPORT.md) y
[docs/PHASE_3_REPORT.md](docs/PHASE_3_REPORT.md).

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
(`services/execution/paper_ledger.py`). Se sigue vela a vela (solo velas
**cerradas**, `close_time <= now` — fix 2026-07-06, ver CHANGELOG) hasta
SL, TP, invalidación técnica o expiración por horizonte, con fee simulada
de 0.1%/lado — sustituto temporal del executor OCO real contra testnet
mientras no existan credenciales (`# DECISION`, sección 10.1 del
documento). Los snapshots de equity se escriben con el tiempo de proceso
y se leen por orden de inserción, filtrados por `environment='paper'`
(fixes 2026-07-06: antes, cierres procesados fuera de orden cronológico
podían perder PnL de la curva de equity, y filas de otros environments
contaminaban los checks de cartera).

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

## Capa fundamental (fase 2, casi cerrada)

Cada `SCAN_INTERVAL_MINUTES` corren, de forma independiente (un fallo en
uno no afecta a los demás ni al ciclo técnico):

- **Ingesta al almacén PIT inmutable** (sección 12.1):
  - **RSS/JSON** (`news_items`) desde anuncios de Binance, CoinDesk y The
    Block (`services/fundamental/ingest_rss.py`), sin credenciales.
  - **Reddit** (`social_items`) desde r/CryptoCurrency + el subreddit de
    cada activo del universo (`services/fundamental/ingest_reddit.py`),
    vía OAuth `client_credentials` (app-only). **Requiere**
    `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` en `.env`; sin ellos no
    hace nada (no falla).
- **Clasificador Ollama** (sección 12.3,
  `services/fundamental/classify.py::classify_pending_items`): hasta
  `FUNDAMENTAL_CLASSIFY_BATCH_SIZE` items sin clasificar (news primero,
  luego social) por corrida → `stance`/`event_types`/`veto`/`summary` en
  `item_classifications`. Verificado en producción contra el Ollama real
  del usuario, no solo en tests con mocks.

**Veto fundamental integrado (sección 12.4)** —
`services/fundamental/veto.py::asset_has_active_veto` es el único punto
de verdad, reutilizado en dos sitios:
- **Bloquea entradas nuevas**: `services/risk/engine.py` trata el veto
  como un check más (`checks["fundamental_veto"]`), igual que el filtro
  de régimen — nunca se salta.
- **Cierra posiciones abiertas**: `paper_ledger.evaluate_exit` cierra
  inmediatamente (`closed_veto`, antes que SL/TP/invalidación) si
  aparece un veto fresco sobre el activo de una posición ya abierta.

Un veto permanece activo `FUNDAMENTAL_VETO_HOURS` (24h por defecto) tras
`classified_at`. **Confirmado en producción**: una noticia real sobre el
desplome del 73% de una empresa con reservas en AVAX se clasificó como
`bearish_strong`/`veto=true`, y `asset_has_active_veto("AVAX", ...)`
bloquea correctamente ese activo en este momento.

**Scorecard semanal** (sección 12.3/16,
`services/fundamental/scorecard.py::compute_weekly_scorecard`, cron
lunes 00:05 UTC): hit-rate y retorno medio firmado de cada `stance`
contra retornos realizados a 4h/24h/72h — decide si la capa aporta señal
real. Vacío hasta que se acumulen semanas de clasificaciones (recién
construido).

**Conectividad con Ollama** (sección 12.3): el contenedor `app` se
conecta al Ollama que corre en el **host** del usuario (no un servicio
nuevo en docker-compose) vía `OLLAMA_HOST=http://host.docker.internal:11434`.
`docker-compose.yml` fija `extra_hosts: host.docker.internal:host-gateway`
para que esto funcione también en Linux (en Docker Desktop/Windows/Mac
ya es automático). `OLLAMA_MODEL=qwen3.5:9b` por defecto — modelo
general ya disponible en el host, sin la sobrecarga de latencia de un
modelo de razonamiento para una tarea de clasificación simple y
frecuente.

> # DECISION (2026-07-06): el `format` de JSON Schema estricto de Ollama
> (`format: {...schema...}`) no funciona con `qwen3.5:9b` en la versión
> de Ollama del usuario (0.31.1) — el modelo lo ignora y devuelve prosa
> libre. Se usa `format: "json"` (JSON suelto) con un prompt que
> enumera los valores válidos, más validación estricta con Pydantic del
> lado Python: un valor fuera de enum hace que ESE item falle
> (fail-closed por item), sin depender de que el grammar-constraint de
> Ollama funcione para cualquier modelo futuro.

**Reddit OAuth**: variables
`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` ya
declaradas en `.env.example`/`app/config.py`, pendientes de rellenar
(crear la app en https://www.reddit.com/prefs/apps, tipo "script").

## Meta-decider + dashboard + memo LLM (fase 3, en progreso)

**Meta-decider** (sección 13): fusión determinista técnico×fundamental
por **tabla de política explícita, no pesos** —
`services/decision/policy.py::POLICY_TABLE`, un único archivo legible.
Solo se consulta si `MODE != technical_only` (variable ya existente en
`app/config.py`) y si el risk engine ya aprobó la entrada (el veto de
fase 2 sigue aplicando siempre, independientemente de `MODE` — ver
`# DECISION` en `services/decision/policy.py` sobre por qué el veto y la
tabla de política son cosas distintas). Puede reducir a la mitad el
tamaño de una entrada o convertirla en `watchlist`/`reject` aunque el
risk engine la hubiera aprobado; el resultado queda auditable en
`decision_logs.decision_jsonb`.

**3 modos de ablación** (`MODE` en `.env`):
- `technical_only` (por defecto): comportamiento idéntico a antes de
  fase 3, la tabla de política ni se consulta.
- `technical_plus_fundamental` / `full`: se consulta la tabla; `full`
  además dispara el memo LLM (ver abajo) cuando se abre una posición.

> `MODE` es hoy puramente informativo salvo por esto — se guarda en
> `decision_logs.mode` para poder comparar periodos históricos con
> distinto modo configurado, pero el sistema NO evalúa los 3 modos en
> paralelo sobre la misma señal (eso sería "shadow mode", descartado
> para esta ronda por complejidad — decisión explícita).

**Dashboard mínimo**: `GET /dashboard` (puerto 8000, expuesto en
`docker-compose.yml`) — página HTML autocontenida (Chart.js por CDN, sin
plantillas ni dependencias nuevas) con curva de equity, resumen de
trading (win rate, profit factor, drawdown), decisiones agrupadas por
modo de ablación y las últimas decisiones. Todo lee de
`services/reporting/dashboard_data.py`, la misma fuente que usa
`scripts/estado.py` (nunca dos cálculos del mismo número).

**Memo LLM opcional** (`USE_REMOTE_LLM`, sección 13): un LLM externo
redacta un resumen del trade a partir de los payloads YA decididos —
nunca influye la decisión. Llama a la API de mensajes de Anthropic vía
`httpx` directamente (sin SDK nuevo). Apagado por defecto y sin API key
configurada — mismo patrón que Reddit: el código está listo, se activa
rellenando `REMOTE_LLM_API_KEY` en `.env` y `USE_REMOTE_LLM=true` +
`MODE=full`.

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
- `httpx`: cliente HTTP async para Telegram, la ingesta RSS/JSON (fase
  2), el clasificador Ollama (fase 2) y el memo LLM de Anthropic (fase
  3) — un único cliente HTTP async para todo, sin SDKs nuevos por
  proveedor.
- `feedparser`: parseo de RSS estándar (CoinDesk, The Block) — librería
  estándar de facto para RSS en Python, sin dependencias pesadas.
- **Fase 3**: ninguna dependencia Python nueva. El dashboard usa
  Chart.js cargado por CDN en el HTML (no es una dependencia del
  proyecto, solo un `<script src>` en la página).

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
  reales de exchange (tickSize/stepSize/minNotional), y el seguimiento
  de posiciones corre a la cadencia de 15 min del ciclo existente en vez
  de los 5 min de la sección 11 (pensados para el monitor real). El veto
  fundamental (fase 2) SÍ está integrado, ver sección "Meta-decider +
  dashboard" arriba. Detalle completo en
  `services/execution/paper_ledger.py`.
- No hay claves de Binance configuradas; no hacen falta para lo que existe
  hoy (los datos usados son endpoints públicos de solo lectura).
- `equity_snapshots` empieza vacío en cada entorno nuevo; el risk engine
  usa `PAPER_STARTING_EQUITY_USDT` como arranque hasta que el paper ledger
  cierre la primera posición (ver Apéndice A).
- El backtest testea cada timeframe (1h/4h) de forma independiente y
  compone los trades out-of-sample de forma secuencial (no simula cartera
  multi-posición real) — limitaciones completas en `backtests/RESULTS.md`.
- **Divergencia backtest/paper en las SALIDAS — corregida el 2026-07-06**:
  el backtest ahora reutiliza literalmente `paper_ledger.evaluate_exit`
  (`backtests/strategy_breakout.py::simulate_trades`) para decidir
  invalidación técnica y salida por tiempo, no solo SL/TP. Los números
  de `backtests/RESULTS.md` están recalculados con el motor corregido
  (expectancy positiva pero más modesta que antes del fix — ver ese
  documento). **Residual sin cubrir**: el backtest sigue sin aplicar el
  filtro de régimen BTC ni los filtros duros del scanner (liquidez/
  spread/frescura) — necesitarían histórico de `market_snapshots`, que
  hoy no se persiste para backtest; su ausencia probablemente
  sobreestima el número de trades reales. Tampoco se ha tocado el risk
  engine: `rr_net >= MIN_RR_NET` (`services/risk/engine.py`) se sigue
  calculando contra el SL, no contra la invalidación (la salida más
  frecuente en la práctica) — sin decidir todavía si merece la pena
  cambiarlo.
- Detalles menores conocidos del paper ledger / backtest (revisión
  2026-07-06, aceptados por ahora): la vela en la que ocurre la entrada
  queda excluida del seguimiento (hasta 4h sin vigilar SL/TP); una salida
  por gap se registra al precio exacto del SL (optimista); las ventanas
  IS/OOS del walk-forward comparten 1 vela de frontera (`df.loc`
  inclusivo); la frescura de datos del scanner solo se comprueba sobre
  velas 1h.
- Fase 2 (capa fundamental) está casi cerrada: almacén PIT + ingesta
  RSS/JSON + clasificador Ollama + veto fundamental (bloquea entradas y
  cierra posiciones) + scorecard semanal, todos funcionando y verificados
  en producción. Sigue pendiente Reddit: `REDDIT_CLIENT_ID`/`SECRET`
  siguen vacíos (el registro de la app en reddit.com/prefs/apps quedó
  bloqueado por el propio proceso de verificación de desarrollador de
  Reddit, fuera de nuestro control), así que esa fuente no aporta items
  todavía (no falla, solo no aporta datos) — el clasificador sigue
  funcionando igual sobre RSS/JSON mientras tanto. El endpoint de
  anuncios de Binance usado no es RSS oficial ni documentado (ver
  `# DECISION` en la sección 12.2 del documento) — puede romperse sin
  aviso; está aislado por fuente (fail-closed local, no tumba las otras
  dos).
- Fase 3 (meta-decider + dashboard) en progreso, `# DECISION` de esta
  ronda: el veto fundamental (fase 2) sigue aplicando SIEMPRE
  independientemente de `MODE` (es una salvaguarda de riesgo, no parte
  de la ablación); solo la tabla de política (fusión stance→acción/
  tamaño) se gatea por `MODE != technical_only`. La ablación es
  "solo-modo-activo": el sistema opera bajo el `MODE` configurado y
  `decision_logs.mode` permite comparar periodos históricos con distinto
  modo — NO se evalúan los 3 modos en paralelo sobre la misma señal
  ("shadow mode", descartado por complejidad para esta ronda). El memo
  LLM no se ha probado contra la API real de Anthropic todavía (sin
  `REMOTE_LLM_API_KEY` configurada) — código verificado con mocks, no en
  producción, a diferencia del clasificador Ollama.
