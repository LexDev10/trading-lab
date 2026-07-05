# Fase 2 — Capa fundamental point-in-time (arranque)

Estado: **arrancada, lejos de cerrada**. Se implementó lo que se puede
construir y verificar honestamente sin credenciales adicionales: el
almacén PIT (`news_items`) y la ingesta RSS/JSON. Reddit y el clasificador
Ollama — el corazón de la sección 12 — quedan explícitamente fuera de
este round.

**Decisión del usuario** que motiva este documento: pausar el resto de
fase 1 (executor OCO real, bloqueado por credenciales de testnet) y
arrancar fase 2 en su lugar, en vez de saltar directo a fase 3 (que
depende de fase 2 — el meta-decider de fase 3 fusiona técnico ×
fundamental; sin fase 2 no hay señal fundamental que fusionar).

De paso, en la misma sesión se implementaron las **alertas de Telegram**
(sección 17) — no es parte de fase 2 del documento, pero el usuario ya
tenía las credenciales y tenía sentido cerrarlo junto con esto.

## Qué se hizo

### Alertas de Telegram (sección 17, no es fase 2, pero misma sesión)

- `notifications/telegram.py`: `send_message(settings, text)` vía
  `httpx`. **Fail-open**: una excepción de red se loguea y no se
  propaga — a diferencia del resto del sistema (fail-closed por
  diseño), un fallo de Telegram nunca debe bloquear una decisión de
  trading ni tumbar un ciclo del scheduler.
- Cuatro disparadores: nueva posición de papel, cierre con PnL
  (`services/execution/paper_ledger.py`), halt y rearme
  (`scripts/halt.py`/`scripts/rearm.py`).
- Resumen diario 22:00 UTC (`services/reporting/daily_summary.py`,
  job nuevo `daily_summary_job` en `app/scheduler.py`): equity,
  drawdown, trades del día, rechazos por motivo, y estado de
  `CORE_ASSETS` aunque no haya setup (sección 21.4) — reutiliza
  `compute_btc_regime` (ya agnóstica al activo) sobre las velas 4h ya
  ingeridas, sin escribir en `regime_log`.
- Alcance deliberadamente acotado: **solo alertas salientes**, sin
  comandos interactivos de Telegram (`/analiza`/`/estado` por Telegram
  quedan fuera — ya existen como CLI, y un listener de long-polling es
  infraestructura adicional que el usuario decidió no priorizar ahora).

### Almacén PIT + ingesta RSS (sección 12.1/12.2)

- Migración `0004`: tabla `news_items` únicamente (`id, source,
  source_url, title, body_text, asset_tags, published_at, fetched_at,
  content_hash UNIQUE, raw_jsonb`). `social_items`,
  `item_classifications` y `classifier_scorecard` **no** se crearon —
  sin Reddit ni clasificador no tendrían ningún consumidor; se añaden en
  su propia migración cuando se construyan.
- `core/schemas/fundamental.py::NewsItem` (Pydantic), mismo patrón que
  `core/schemas/market.py`.
- `services/fundamental/ingest_rss.py`: separación red/parseo (mismo
  patrón que `services/data/binance_market_data.py`) para poder testear
  el parseo con fixtures grabadas sin red.
  - CoinDesk y The Block: RSS estándar vía `feedparser`.
  - Binance: **no tiene RSS público estable** para anuncios. Se usa el
    endpoint JSON que consume su propia web de anuncios
    (`bapi/composite/v1/public/cms/article/catalog/list/query`),
    verificado manualmente que responde con artículos reales. No es una
    API documentada oficialmente — riesgo explícito de que cambie de
    forma sin aviso, documentado como `# DECISION` en la sección 12.2.
  - `extract_asset_tags`: heurística determinista (regex de palabra
    completa contra alias conocidos: ticker + nombre común) para filtrar
    qué items son relevantes por activo. **No es la clasificación real**
    (`stance`/`event_types`) — eso lo hará el clasificador Ollama,
    todavía sin construir.
  - `ingest_all`: cada fuente falla de forma independiente (fail-closed
    por fuente, no global) — un feed caído no debe tumbar las otras dos
    ni el ciclo técnico.
- `app/scheduler.py::fundamental_ingest_job`: job independiente de
  `market_cycle_job`, misma cadencia (`SCAN_INTERVAL_MINUTES`).

## Qué quedó fuera (y por qué)

- **Reddit OAuth Data API** (r/CryptoCurrency + subreddit del activo,
  sección 12.2): necesita registrar una app en Reddit (client_id/secret),
  credenciales que el usuario todavía no ha creado. **Actualización
  (2026-07-03, tercera ronda)**: las variables `REDDIT_CLIENT_ID`/
  `REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` ya están declaradas en
  `app/config.py`/`.env`/`.env.example` (vacías) — solo falta que el
  usuario cree la app en reddit.com/prefs/apps y las rellene, y construir
  `services/fundamental/ingest_reddit.py`.
- **Clasificador Ollama** (sección 12.3): necesitaba decidir cómo el
  contenedor `app` llega a un servidor Ollama. **Resuelto (2026-07-03,
  tercera ronda)**: conecta al Ollama que corre en el host del usuario
  vía `OLLAMA_HOST=http://host.docker.internal:11434` (nuevo en
  `app/config.py`; `docker-compose.yml` añade `extra_hosts` para que
  funcione también en Linux) — verificado con `curl` desde dentro del
  contenedor, responde 200 y ve los modelos del host. Modelo elegido:
  `qwen3.5:9b` (ya descargado en el host; se descartó `deepseek-r1:14b`
  por ser un modelo de razonamiento, de más latencia, pensado para
  problemas de lógica/matemáticas y no para clasificación simple y
  frecuente). Lo que sigue faltando es el clasificador en sí: prompt,
  JSON Schema estricto, parseo de la respuesta, y escritura en
  `item_classifications` (tabla todavía no creada).
- `item_classifications`, `social_items`, `classifier_scorecard`: schema
  no creado (sin consumidor todavía — se añaden cuando se construya el
  clasificador/Reddit).
- Veto fundamental (`RejectionReason.fundamental_veto`, ya existe el
  enum) sin ningún emisor todavía — llega junto con el clasificador.

## Tests

**9 tests unitarios nuevos** (sin DB):
- `test_telegram.py`: no-op sin credenciales, arma bien el payload con
  credenciales, fail-open ante una excepción de red simulada.
- `test_ingest_rss.py`: `extract_asset_tags` (nombre completo vs ticker,
  case-insensitive, frontera de palabra — "SOLD" no matchea "SOL"),
  `content_hash` determinista, parseo de RSS/JSON contra fixtures
  grabadas (`tests/fixtures/rss_sample.xml`,
  `tests/fixtures/binance_announcements_sample.json`).

**3 tests de integración nuevos** (Postgres real):
- `test_ingest_rss.py::test_persist_news_items_is_idempotent`: reingestar
  los mismos items no duplica filas (`ON CONFLICT DO NOTHING`).
- `test_daily_summary.py`: agregación de rechazos por motivo y trades del
  día. **Importante**: afirma sobre el DELTA introducido por los datos
  sembrados, no sobre valores absolutos — el resumen diario agrega datos
  GLOBALES y este test corre contra la misma Postgres que la app real en
  paralelo (ver hallazgo abajo).

Total: **66/66 unitarios + 5/5 integración** en verde. `mypy --strict`
ampliado a `notifications/`, `services/fundamental/`,
`services/reporting/` (22 archivos, sin errores).

## Hallazgo durante la verificación: el paper ledger operó solo, de principio a fin

Durante esta sesión, con el stack corriendo en background mientras se
investigaba/implementaba, **el scheduler abrió automáticamente una
posición de papel real** en `ETHUSDT` — primera entrada 100% autónoma del
sistema, sin intervención manual — **y más tarde la cerró también sola**
(`closed_sl`, pnl −6.6444 USDT, confirmado con `scripts.estado`). Buena
validación de que el paper ledger (sesión anterior) funciona de extremo a
extremo en producción, no solo en tests — abrió, siguió y cerró una
posición real sin que nadie lo tocara.

Esto expuso fragilidad preexistente en dos sitios:
1. `tests/integration/test_killswitch.py`: asumía una base de datos
   "limpia" con exposición cero; con una posición real abierta el check
   `correlated_exposure` falla legítimamente, tumbando la aserción
   `verdict.approved is True`. Corregido para afirmar únicamente sobre
   `checks["system_not_halted"]` (lo único que ese test ejercita según su
   propio nombre/docstring).
2. `tests/integration/test_paper_ledger.py` (sesión anterior, paper
   ledger): anclaba sus timestamps a una fecha fija en el pasado
   (2026-01-01). En cuanto la posición real cerró con un timestamp
   posterior, esa fecha dejó de ser "la última equity" (`equity_snapshots`
   no tiene FK — se lee por `ts` más reciente sobre toda la tabla) y las
   aserciones de equity del test empezaron a leer el dato real en vez del
   propio del test. Corregido anclando todos los timestamps a
   `datetime.now(tz=UTC)` con offsets relativos (`+3h`), que por
   construcción siempre quedan por delante de cualquier dato real.

Mismo criterio aplicado preventivamente en `test_daily_summary.py`
(afirma sobre deltas introducidos por los datos sembrados, no sobre
valores absolutos, ya que los rechazos/trades del día son agregados
globales y la app real los sigue escribiendo en paralelo). Lección
general para cualquier test de integración futuro que toque
`equity_snapshots`: anclar siempre a `now()` real, nunca a una fecha
fija — confirmado repitiendo la suite de integración dos veces seguidas
tras el fix, sin fallos.

## Cómo reproducir la verificación

```bash
docker compose up -d --build   # aplica migración 0004
docker compose run --rm --no-deps app uv run pytest -v
docker compose exec app uv run pytest tests/integration -v
docker compose run --rm --no-deps app uv run mypy
docker compose exec app uv run python -m scripts.halt "prueba"
docker compose exec app uv run python -m scripts.rearm
docker compose exec app uv run python -m scripts.estado
```
Confirmar por `psql` que `news_items` se llena con artículos reales de
las 3 fuentes, y en el Telegram real del usuario que llegan los mensajes
de halt/rearme.
