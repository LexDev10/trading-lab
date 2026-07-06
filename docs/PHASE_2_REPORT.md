# Fase 2 — Capa fundamental point-in-time (arranque)

Estado: **arrancada, lejos de cerrada**. Se implementó lo que se puede
construir y verificar honestamente sin credenciales adicionales: el
almacén PIT (`news_items` + `social_items`) y la ingesta RSS/JSON +
Reddit, además de la conectividad con Ollama. El clasificador en sí (el
corazón de la sección 12) queda fuera todavía, y la ingesta de Reddit
está lista pero inactiva hasta que el usuario complete el registro de
desarrollador de Reddit (bloqueante externo, no de código — ver más
abajo).

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

### Ingesta de Reddit (2026-07-05, sección 12.1/12.2)

- Migración `0005`: tabla `social_items` (`platform, subreddit, post_id
  UNIQUE, title, body_text, score_at_fetch, num_comments_at_fetch,
  published_at, fetched_at, raw_jsonb`). Idempotente por `post_id`
  (identificador propio de Reddit — no hace falta un `content_hash`
  calculado como en `news_items`).
- `core/schemas/fundamental.py::SocialItem` (Pydantic).
- `services/fundamental/ingest_reddit.py`: mismo patrón que
  `ingest_rss.py` (separación red/parseo, fail-closed por subreddit).
  **Grant OAuth `client_credentials`** (app-only, sin usuario/contraseña
  de Reddit) — solo necesitamos lectura pública de listados, nunca
  acciones en nombre de una cuenta; evita guardar credenciales de una
  cuenta personal. Ingesta: r/CryptoCurrency + el subreddit de cada
  activo del universo (`ASSET_SUBREDDITS`, mapeo best-effort ya que la
  sección 12.2 no los nombra explícitamente — un nombre desactualizado
  solo produce 0 items para ese activo, no rompe nada).
- Sin `REDDIT_CLIENT_ID`/`SECRET` configurados, `ingest_all` no intenta
  nada por red (no es un error) — mismo criterio que
  `notifications/telegram.py` sin credenciales de Telegram. Verificado en
  vivo: el log del job no muestra ninguna clave `reddit_*` cuando las
  credenciales están vacías.
- `app/scheduler.py::_fundamental_ingest`: RSS y Reddit corren en
  sesiones de DB separadas dentro del mismo job — si Reddit falla
  catastróficamente no deshace lo que RSS ya confirmó.
- **Bloqueante externo, no de código**: al intentar registrar la app en
  reddit.com/prefs/apps, Reddit exigió completar su propio proceso de
  verificación de desarrollador ("Responsible Builder Policy" + registro
  de API aparte) antes de dejar crear la app — fuera de nuestro control,
  pendiente de que el usuario lo complete. El código en sí está terminado
  y verificado (no-op limpio sin credenciales); en cuanto el usuario
  rellene `REDDIT_CLIENT_ID`/`SECRET` en `.env`, la ingesta empieza a
  traer datos reales sin tocar más código.

## Qué quedó fuera (y por qué)

- **Reddit OAuth Data API**: código completo (ver arriba), bloqueado por
  el registro de desarrollador de Reddit — pendiente del usuario, no de
  trabajo de código.
- **Conectividad con Ollama**: resuelta (ver README, sección
  "Capa fundamental") — `OLLAMA_HOST=http://host.docker.internal:11434`,
  modelo `qwen3.5:9b` (se descartó `deepseek-r1:14b` por ser un modelo de
  razonamiento, más lento y pensado para lógica/matemáticas, no para
  clasificación simple y frecuente), verificado con `curl` desde dentro
  del contenedor.
- ~~**Clasificador en sí** (sección 12.3)~~ — **hecho el 2026-07-06**,
  ver "Continuación" al final de este documento y `CHANGELOG.md`.
- ~~`item_classifications`, `classifier_scorecard`: schema no creado~~ —
  **hecho el 2026-07-06** (migración `0006_item_classifications.py`).
- ~~Veto fundamental sin ningún emisor todavía~~ — **hecho el
  2026-07-06**, integrado en el risk engine y en el paper ledger.

## Tests

**11 tests unitarios nuevos** (sin DB):
- `test_telegram.py`: no-op sin credenciales, arma bien el payload con
  credenciales, fail-open ante una excepción de red simulada.
- `test_ingest_rss.py`: `extract_asset_tags` (nombre completo vs ticker,
  case-insensitive, frontera de palabra — "SOLD" no matchea "SOL"),
  `content_hash` determinista, parseo de RSS/JSON contra fixtures
  grabadas (`tests/fixtures/rss_sample.xml`,
  `tests/fixtures/binance_announcements_sample.json`).
- `test_ingest_reddit.py` (2026-07-05): parseo de listado contra fixture
  grabada (`tests/fixtures/reddit_listing_sample.json`), y no-op
  confirmado sin credenciales — monkeypatch de `httpx.AsyncClient` que
  lanza si se llega a invocar (garantiza que de verdad no toca la red).

**6 tests de integración nuevos** (Postgres real):
- `test_ingest_rss.py::test_persist_news_items_is_idempotent`: reingestar
  los mismos items no duplica filas (`ON CONFLICT DO NOTHING`).
- `test_ingest_reddit.py::test_persist_social_items_is_idempotent`
  (2026-07-05): mismo criterio, idempotencia por `post_id`.
- `test_daily_summary.py`: agregación de rechazos por motivo y trades del
  día. **Importante**: afirma sobre el DELTA introducido por los datos
  sembrados, no sobre valores absolutos — el resumen diario agrega datos
  GLOBALES y este test corre contra la misma Postgres que la app real en
  paralelo (ver hallazgo abajo).

Total: **68/68 unitarios + 6/6 integración** en verde. `mypy --strict`
ampliado a `notifications/`, `services/fundamental/`,
`services/reporting/` (23 archivos, sin errores).

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
docker compose up -d --build   # aplica migraciones 0004 y 0005
docker compose run --rm --no-deps app uv run pytest -v
docker compose exec app uv run pytest tests/integration -v
docker compose run --rm --no-deps app uv run mypy
docker compose exec app uv run python -m scripts.halt "prueba"
docker compose exec app uv run python -m scripts.rearm
docker compose exec app uv run python -m scripts.estado
docker compose exec app curl http://host.docker.internal:11434/api/tags
```
Confirmar por `psql` que `news_items` se llena con artículos reales de
las 3 fuentes RSS/JSON, y en el Telegram real del usuario que llegan los
mensajes de halt/rearme. `social_items` se queda vacía hasta que el
usuario complete el registro de desarrollador de Reddit y rellene
`REDDIT_CLIENT_ID`/`SECRET` — el log de `fundamental_ingest_job` no debe
mostrar ninguna excepción por eso (no-op silencioso, confirmado).

---

## Continuación (2026-07-06): clasificador Ollama + veto + scorecard

**Decisión del usuario** que motiva esta sesión: seguir con Fase 2 hoy
(clasificador Ollama, no depende de Reddit) y dejar Fase 3/dashboard
para después de cerrar Fase 2 — otra vez, decisión explícita de no
adelantar fases. Las credenciales de Reddit las da el usuario más
adelante.

Con esto, Fase 2 queda **casi cerrada**: solo falta que el usuario
rellene `REDDIT_CLIENT_ID`/`SECRET` (bloqueante externo, no de código,
igual que en la sesión anterior).

### Qué se hizo

Detalle técnico completo en `CHANGELOG.md` (entrada 2026-07-06,
"Fase 2: clasificador Ollama + veto fundamental + scorecard semanal").
Resumen:

- **Esquema**: migración `0006_item_classifications.py` —
  `item_classifications` (con `asset_tags` desnormalizado, `# DECISION`
  fuera del schema sketch de la sección 12.1 para evitar un JOIN
  polimórfico en cada ciclo del scanner) y `classifier_scorecard`.
- **Clasificador** (`services/fundamental/classify.py`): llama a Ollama
  con `format: "json"` (no el JSON Schema estricto — no funciona con
  `qwen3.5:9b` en la versión de Ollama del usuario, ver `# DECISION` en
  el código y en `CHANGELOG.md`) y valida la respuesta con Pydantic,
  fail-closed por item.
- **Veto fundamental** (sección 12.4): un único punto de verdad
  (`services/fundamental/veto.py::asset_has_active_veto`) que bloquea
  entradas nuevas (`services/risk/engine.py`) y cierra posiciones
  abiertas (`services/execution/paper_ledger.py`, nuevo
  `TradeStatus.closed_fundamental_veto`).
- **Scorecard semanal** (`services/fundamental/scorecard.py`): hit-rate
  y retorno firmado por `(stance, horizon)`, cron lunes 00:05 UTC.
- Dos jobs nuevos en `app/scheduler.py`.

### Verificación en producción (no solo tests)

Con el stack ya corriendo en background durante la sesión, el
scheduler ejecutó `fundamental_classify_job` de verdad contra el Ollama
real del usuario: **47 noticias reales clasificadas**, incluida una
sobre el desplome del 73% de una empresa con reservas en AVAX,
correctamente marcada `bearish_strong`/`veto=true`. Confirmado con
`asset_has_active_veto` que AVAX queda bloqueado ahora mismo — el
sistema completo (ingesta → clasificación → veto → risk engine)
funciona de punta a punta con datos reales, no solo en tests con mocks.

### Hallazgo: los tests de integración pueden contaminar datos reales

Mismo patrón que el incidente ya documentado arriba (paper ledger
operando solo en background): la primera versión de
`tests/integration/test_classify.py` llamaba a `classify_pending_items`
con el `batch_size` de producción (10) contra el Postgres COMPARTIDO con
la app real — **contaminó 9 noticias reales** con la respuesta fake del
mock antes de que se detectara y limpiara a mano. Corregido: los tests
fuerzan `fundamental_classify_batch_size=1` y usan un `fetched_at` muy
anterior a cualquier dato real, para garantizar que el item de test es
siempre el único elegido. Lección para cualquier test de integración
futuro que llame a una función de "procesar lo pendiente" sobre una
tabla compartida con la app real: acotar el batch explícitamente, no
asumir que el dataset de test está aislado.

### Qué queda fuera todavía

- Reddit: bloqueado por el registro de desarrollador (usuario, no
  código) — igual que antes.
- El scorecard semanal está vacío hasta que se acumulen semanas de
  clasificaciones (recién construido).
- No se ha probado el "cierre anticipado por veto" con una posición de
  papel real abierta en producción (sí con tests unitarios/integración);
  se validará cuando el sistema abra una posición nueva y aparezca un
  veto mientras está abierta.

### Tests

93 unit + 13 integración en verde (`test_classify.py`,
`test_scorecard.py`, `test_veto.py` nuevos; casos nuevos en
`test_risk_engine.py`/`test_paper_ledger.py`). `mypy` sin categorías de
error nuevas frente al baseline del fix del bug #4 (167→225 líneas,
todas del mismo patrón de deuda preexistente en tests, más dos errores
reales corregidos en `classify.py`).
