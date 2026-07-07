# Changelog

Registro cronológico de lo implementado en el proyecto. Formato:
`[YYYY-MM-DD HH:MM] Descripción`.

## 2026-07-07

- **[2026-07-07]** **Corrección de los bugs #10-#18 de
  `docs/CODE_REVIEW_2026-07-07.md`** (revisión de código posterior a la
  del 2026-07-06). Migraciones nuevas: `0007_pending_fill_dedupe_processed_at`,
  `0008_veto_published_at_source`, `0009_scorecard_unique_constraint`.
  Verificado en Docker: 128 unit + 25 integration tests, todos en verde;
  mypy sin categorías de error nuevas (el resto es la misma deuda
  preexistente de anotaciones en tests, documentada el 2026-07-06).

  - **Bug #10 (posiciones duplicadas)**: `PortfolioSnapshot` gana
    `asset_has_open_position`; nuevo check
    `checks["no_open_position_same_asset"]` en el risk engine
    (`RejectionReason.position_already_open`, nuevo). Segunda capa:
    columna `trade_entries.signal_candle_close_time` +
    `paper_ledger.signal_already_traded` — el scanner nunca vuelve a
    abrir sobre la MISMA vela de ruptura.
  - **Bug #11 (fill optimista)**: `open_position` ya NO llena
    inmediatamente a `entry_ref` — registra una orden `status='pending'`
    (columnas nuevas `entry_zone_low`/`entry_zone_high`). Función nueva
    `paper_ledger.evaluate_pending_fill` decide el fill vela a vela
    contra la `entry_zone`, respetando `ENTRY_TTL_MINUTES` (existía en
    `Settings` desde el origen del proyecto pero nunca se usó). Estado
    nuevo `TradeStatus.expired` si no hay fill dentro del TTL. El
    backtest (`strategy_breakout.py::simulate_trades`) reutiliza la
    MISMA función (regla sección 6) — la expectancy de
    `backtests/RESULTS.md` queda desactualizada por este fix y debe
    recalcularse antes de fiarse de ella para decisiones.
    **DECISION documentada en el código**: `ENTRY_TTL_MINUTES` (45) es
    menor que cualquier timeframe operado (1h/4h) — `evaluate_pending_fill`
    admite deliberadamente la vela EN CURSO (a diferencia de
    `evaluate_exit`), o ningún fill ocurriría jamás.
  - **Bug #12 (orden del ciclo)**: `market_cycle_job` ahora procesa
    cierres (`update_open_positions`) ANTES de escanear nuevas entradas
    (`run_scan_cycle`) — antes al revés, así que el estado de cartera que
    veía el risk engine (cooldown de 2 SL, pérdida diaria) no reflejaba
    cierres ya ocurridos en el mismo ciclo.
  - **Bug #13 (clasificador bloqueado)**: `classify.py` persiste ahora una
    fila neutra (`stance=unknown`, `veto=False`,
    `summary=classification_failed`) cuando un item falla, en vez de
    dejarlo pendiente para siempre (head-of-line blocking del batch).
  - **Bug #14 (scorecard)**: descarta puntos sin vela suficiente para el
    horizonte (antes truncaba silenciosamente); upsert por
    `(week, stance, horizon)` — constraint único nuevo, re-ejecutar el
    job de la misma semana ya no duplica filas; itera TODOS los
    `asset_tags` de un item, no solo el primero.
  - **Bug #15 (notificaciones fantasma)**: `run_scan_cycle` evalúa cada
    activo dentro de un `try/except` (un fallo en un activo ya no tumba
    el resto del universo). `open_position`/`close_position`/
    `update_open_positions`/`run_scan_cycle` ya no llaman a Telegram
    directamente — devuelven el texto y el scheduler lo envía DESPUÉS de
    `session.commit()`.
  - **Bug #16 (veto pisa SL/TP)**: en `evaluate_exit`, el veto fundamental
    ahora se comprueba DESPUÉS del bucle de velas (SL/TP/invalidación) —
    antes pisaba una salida que ya había ocurrido en una vela anterior.
  - **Bug #17 (pérdida diaria por tiempo de vela)**: columna nueva
    `trade_exits.processed_at` (tiempo de PROCESO, monotónico, igual
    criterio que el fix del bug #1 en la curva de equity);
    `daily_loss_limit` y el resumen diario (`daily_summary.py`) agregan
    por `processed_at`, nunca por `exit_time`.
  - **Bug #18 (veto LLM sobre contenido no autenticado)**: nueva función
    `asset_has_active_closing_veto` — el cierre FORZOSO de una posición
    abierta exige `item_kind='news'` corroborado por
    `FUNDAMENTAL_VETO_MIN_SOURCES` (2 por defecto) fuentes independientes
    distintas; un veto de fuente `social` (Reddit) sigue bloqueando
    ENTRADAS nuevas (sin cambios ahí) pero ya no puede cerrar una
    posición por sí solo. Columnas nuevas `item_classifications.published_at`/
    `source`; la ventana de decaimiento del veto (`FUNDAMENTAL_VETO_HOURS`)
    se mide ahora desde `published_at` (con fallback a `classified_at`),
    no desde `classified_at` — un backlog viejo clasificado tarde ya no
    genera vetos "frescos" de noticias de hace días.

  **Deuda operativa**: igual que con los bugs 1-4, cualquier histórico de
  paper trading generado ANTES de este fix (bugs #10-#12 activos) debe
  purgarse — el contador de los gates (≥60 días / ≥30 trades, sección 15)
  se reinicia desde este commit. `backtests/RESULTS.md` necesita
  recalcularse con el motor de fill corregido antes de usarse para
  decidir nada.

## 2026-07-06

- **[2026-07-06]** **Fase 3 (en progreso): meta-decider + dashboard +
  memo LLM opcional** (sección 13/4/19).
  - **Tabla de política** (`services/decision/policy.py`, paquete
    nuevo): fusión determinista técnico×fundamental literal de la
    sección 13, un único archivo legible (`POLICY_TABLE`). La fila
    "veto" de la tabla original se omite a propósito: ya la cubre el
    risk engine desde fase 2 (`checks["fundamental_veto"]`) — repetirla
    aquí sería una segunda implementación del mismo check.
    `evaluate_policy(conviction, stance)` cubre los 18 pares posibles
    (incluido `moderate`+`bullish_weak`, no cubierto explícitamente por
    la tabla de ejemplo del spec → watchlist, `# DECISION`).
  - **`# DECISION` importante**: el veto fundamental (fase 2) sigue
    aplicando SIEMPRE, independientemente de `MODE` — es una salvaguarda
    de riesgo (hackeos, delistings), no parte de la ablación. Solo la
    tabla de política (la pieza nueva) se gatea por
    `MODE != technical_only`. Confirmado con el usuario: ablación
    "solo-modo-activo" esta ronda (sin shadow-mode — no se evalúan los 3
    modos en paralelo sobre la misma señal).
  - `services/fundamental/veto.py::get_latest_stance`: misma ventana de
    frescura que `asset_has_active_veto` (`fundamental_veto_hours`).
    **Bug encontrado y corregido durante los tests**: ni esta función ni
    `asset_has_active_veto` acotaban `classified_at <= now` (solo el
    límite inferior de la ventana) — sin este límite superior, una
    clasificación posterior a `now` (datos de otra ventana temporal,
    jobs retrasados) contaba como ya conocida en el momento de la
    decisión, violando el principio anti look-ahead que ya aplica en
    todo el resto del sistema (sección 12.1). Corregido en ambas
    funciones.
  - **Enganche en `services/scanner/scanner.py`**: nuevo
    `apply_fundamental_policy` (`None` si `MODE=technical_only` o si el
    risk engine no aprobó). `decide_final_action` (compartida con
    `scripts/analiza.py`, sección 6) gana un `policy_outcome` opcional
    con default `None` — compatibilidad total con el comportamiento
    anterior. Un `size_multiplier` != 1 se aplica copiando el
    `RiskVerdict` (`model_copy`) antes de `paper_ledger.open_position`,
    sin tocar su firma. El resultado de la fusión (`stance`,
    `policy_action`, `size_multiplier`) se guarda en
    `DecisionRecord.decision` — campo del schema que existía desde fase
    1 sin usarse nunca hasta ahora.
  - **Memo LLM opcional** (`services/reporting/llm_memo.py`): llama a la
    API de mensajes de Anthropic vía `httpx` (sin SDK nuevo, mismo
    criterio que Ollama). `USE_REMOTE_LLM=false`/sin
    `REMOTE_LLM_API_KEY` por defecto — no toca la red (mismo patrón que
    Reddit/Telegram). Se dispara solo en `MODE=full` al abrir una
    posición, y se envía por Telegram como mensaje aparte.
  - **Dashboard mínimo** (`GET /dashboard`, `app/dashboard.py` +
    `services/reporting/dashboard_data.py`): página HTML autocontenida
    (Chart.js por CDN, sin plantillas ni dependencias Python nuevas) con
    curva de equity, resumen de trading, decisiones por modo de
    ablación y últimas decisiones. Refactor sin cambio de comportamiento:
    la fórmula de win_rate/profit_factor de `scripts/estado.py` se
    extrajo a `compute_closed_trades_summary` (una sola fuente de
    verdad, reutilizada por ambos).
  - **Verificado con datos reales de producción**: `get_latest_stance`/
    `asset_has_active_veto` contra una clasificación real (una noticia
    sobre el desplome del 73% de una empresa con reservas en AVAX,
    `bearish_strong`/`veto=true`) — con `MODE=technical_plus_fundamental`
    y `conviction=strong` hipotético, la tabla de política devuelve
    `reject`, exactamente la fila de la sección 13. El endpoint
    `/dashboard` renderiza con datos reales de producción (690+
    decisiones ya registradas hoy).
  - Tests nuevos: `test_policy.py`, `test_scanner.py`, `test_llm_memo.py`
    (unit); `test_dashboard_data.py`, extensión de `test_veto.py`
    (integración, con el mismo criterio de asserts-por-delta que
    `test_daily_summary.py` — Postgres compartido con la app real en
    background). 122 unit + 21 integración en verde; `mypy` sin
    categorías de error nuevas (solo deuda preexistente en los propios
    tests nuevos).
  - **Pendiente para cerrar Fase 3**: probar el memo LLM contra la API
    real de Anthropic (solo verificado con mocks); confirmar en
    producción un `size_multiplier` aplicado de verdad al abrir una
    posición (todavía no ha coincidido una señal real con
    `MODE!=technical_only`, ya que `technical_only` sigue siendo el
    modo por defecto).

- **[2026-07-06]** **Fase 2: clasificador Ollama + veto fundamental +
  scorecard semanal** (secciones 12.3/12.4/16). Único trabajo que
  faltaba para cerrar Fase 2 salvo Reddit (bloqueado por credenciales,
  sin tocar en esta ronda).
  - **Esquema** (migración `0006_item_classifications.py`):
    `item_classifications` (sección 12.1 + columna extra `asset_tags`,
    ver `# DECISION` en la migración — evita un JOIN polimórfico contra
    `news_items`/`social_items` en cada ciclo del scanner) y
    `classifier_scorecard` (sección 16).
  - **Clasificador** (`services/fundamental/classify.py`): prompt en
    español que enumera los valores válidos de `stance`/`event_types`
    (`core/enums.py`, sin duplicar la lista), llama a Ollama y valida
    la respuesta con Pydantic (fail-closed por item si el modelo
    devuelve algo fuera de esquema — mismo criterio que
    `ingest_rss.ingest_all` fail-closed por fuente).
    **`# DECISION`**: el `format` de JSON Schema estricto de Ollama
    (`format: {...schema...}`) se probó contra el Ollama real del
    usuario (v0.31.1, `qwen3.5:9b`) y el modelo lo ignora por completo
    (devuelve prosa libre incluso con `think: false`); `format: "json"`
    (JSON suelto) sí funciona de forma fiable con un prompt explícito.
    Se usa JSON suelto + validación Pydantic estricta del lado Python,
    más robusto que depender de que el grammar-constraint de Ollama
    funcione para cualquier modelo futuro.
  - **Veto fundamental** (sección 12.4), un único punto de verdad
    (`services/fundamental/veto.py::asset_has_active_veto`) reutilizado
    en dos sitios (regla crítica, sección 6):
    - Bloquea entradas nuevas: `RiskInput.fundamental_veto_active` →
      `evaluate_risk` lo trata como un check más
      (`checks["fundamental_veto"]`), igual que `regime_filter`.
    - Cierra posiciones abiertas: `paper_ledger.evaluate_exit` gana
      `veto_active: bool = False` — si es `True`, cierra ANTES de
      comprobar SL/TP/invalidación, con el nuevo
      `TradeStatus.closed_fundamental_veto` (valor de enum acortado a
      `"closed_veto"`: `trade_entries.status`/`trade_exits.exit_type`
      son `String(20)`, y el nombre completo no entraba). El backtest
      nunca activa el flag (la capa fundamental no se backtestea,
      sección 14).
  - **Scorecard semanal** (`services/fundamental/scorecard.py`, cron
    lunes 00:05 UTC): hit-rate y retorno medio FIRMADO por
    `(stance, horizon)` — `bullish_*` acierta si el retorno futuro > 0,
    `bearish_*` si es < 0. **`# DECISION`**: `neutral`/`unknown` se
    excluyen (ninguno hace una predicción direccional que evaluar).
    Vacío hasta que se acumulen semanas de datos clasificados.
  - Nuevos jobs en `app/scheduler.py`: `fundamental_classify_job` (misma
    cadencia que la ingesta) y `classifier_scorecard_job` (semanal).
  - **Verificado en producción, no solo en tests**: con el clasificador
    corriendo en vivo contra el Ollama real del usuario, clasificó 47
    noticias reales ya ingeridas, incluida una noticia real sobre el
    desplome del 73% de una empresa con reservas en AVAX, correctamente
    marcada `bearish_strong`/`veto=true` — `asset_has_active_veto`
    bloquea AVAX en este momento, confirmado en vivo.
  - **Hallazgo de la sesión (mismo patrón que el incidente ya
    documentado con el paper ledger en `docs/PHASE_2_REPORT.md`)**: los
    tests de integración corren contra el MISMO Postgres que usa la app
    real en background. La primera versión de
    `tests/integration/test_classify.py` usaba el `batch_size` por
    defecto (10) y **contaminó 9 noticias reales** con la respuesta fake
    del mock (limpiado a mano). Corregido: los tests fuerzan
    `fundamental_classify_batch_size=1` y usan un `fetched_at` muy
    anterior a cualquier dato real, para que el item de test sea siempre
    el único elegido (`_pending_news` ordena por `fetched_at`
    ascendente). El test de idempotencia tampoco vuelve a llamar a
    `classify_pending_items` una segunda vez (con backlog real
    compartido, una segunda pasada siempre encuentra ALGO que
    clasificar) — verifica en su lugar que `_pending_news` excluye el
    item ya clasificado.
  - Tests: `test_classify.py` (unit + integración), caso nuevo
    `fundamental_veto` en `test_risk_engine.py`, casos nuevos de
    `veto_active` en `test_paper_ledger.py`, `test_scorecard.py` (unit +
    integración), `test_veto.py` (integración). 93 unit + 13 integración
    en verde; `mypy` sin categorías de error nuevas frente al baseline
    del fix del bug #4 (solo la misma deuda preexistente de tests, más
    dos errores reales corregidos: una variable de bucle reutilizada
    con tipos incompatibles en `classify.py`).

- **[2026-07-06]** **Bug #4 corregido: divergencia backtest/paper en las
  SALIDAS** (prioridad alta, sección 6 — "divergencia backtest/live es
  un bug de primera clase"). El backtest (`backtests/
  strategy_breakout.py`) solo modelaba SL/TP vía
  `vectorbt.Portfolio.from_signals(sl_stop=..., tp_stop=...)`, ciego a
  las otras dos salidas que sí aplica `paper_ledger.evaluate_exit`:
  invalidación técnica (cierre de vela < `range_high` de la señal — la
  salida más frecuente y mucho más cercana que el SL, porque la entrada
  queda por encima de ese nivel) y salida por tiempo (horizonte máximo,
  sección 10.1). `backtests/RESULTS.md` no representaba la operativa
  real.
  - **Fix**: `run_portfolio` (vectorbt) se sustituye por
    `simulate_trades`, que para cada señal llama DIRECTAMENTE a
    `paper_ledger.evaluate_exit` — mismo código, no una reimplementación
    — para decidir cuándo y a qué precio sale cada trade. `vectorbt`
    deja de usarse en la ruta de decisión. Para permitir la reutilización
    sin acoplar el backtest al ORM: `evaluate_exit` pasa a tipar su
    parámetro `entry` como un `Protocol` (`PaperEntryLike`) en vez de
    `TradeEntry`; `_max_hold` se hace pública (`max_hold_for_horizon`); y
    la aritmética de fees/PnL de `close_position` se extrae a
    `compute_trade_pnl` (misma fórmula, reutilizada por ambos). También
    se hace pública `signal_builder._horizon_for_timeframe` →
    `horizon_for_timeframe`.
  - Si el horizonte de una señal cae después de la última vela
    disponible en la ventana simulada, el trade queda sin resolver
    (censura por límite de datos) y se descarta — de paso resuelve la
    limitación conocida #9 (trades abiertos al final de la ventana
    contaminando la expectancy del walk-forward).
  - El fee del backtest pasa a leerse de `settings.taker_fee` (antes una
    constante `DEFAULT_FEES` propia e independiente de `settings`, un
    mini-riesgo de divergencia adicional). El slippage pesimista
    (`DEFAULT_SLIPPAGE`, 2bps) sigue siendo específico del backtest — el
    paper ledger no lo necesita porque simula fills contra precios reales
    de vela, no una ejecución hipotética.
  - **Resultados recalculados** (`backtests/RESULTS.md`, mismo histórico
    de 803 días ya en DB, sin necesidad de volver a descargar): 738 trades OOS
    (antes 322), win rate 28.0% (antes 55.6% — la invalidación corta la
    mayoría de los trades antes del TP), expectancy neta **+0.096%** por
    trade (antes +0.327% — sigue siendo positiva, la hipótesis de la
    sección 3.1 no queda falsada, pero con un margen bastante más
    estrecho), profit factor 1.99, max drawdown −7.03%.
  - **Fuera de alcance de este fix** (residual, documentado en
    `README.md`/`RESULTS.md`): el backtest sigue sin aplicar el filtro de
    régimen BTC ni los filtros duros del scanner (liquidez/spread/
    frescura) — requieren histórico de `market_snapshots`, que hoy no se
    persiste para backtest. Tampoco se ha tocado `rr_net >= MIN_RR_NET`
    del risk engine (sigue calculándose contra el SL, no contra la
    invalidación).
  - Tests: `tests/unit/test_backtest_regression.py` reescrito —
    `generate_signals` expone ahora precios absolutos
    (`entry_ref`/`sl`/`tp`/`invalidation_level`); nuevos casos que
    prueban que `simulate_trades` cierra por invalidación y descarta
    trades censurados por falta de datos (antes solo se probaba el
    camino SL/TP). Verificado en Docker: 72 unit + 6 integration pasan;
    `mypy` no añade categorías de error nuevas (solo el mismo patrón de
    deuda preexistente en tests: anotaciones de retorno, `_env_file`).

- **[2026-07-06]** **Revisión de código completa: 3 bugs corregidos**
  (contabilidad de equity, vela en curso en salidas, mezcla de
  environments) **+ 2 limitaciones documentadas** (divergencia
  backtest/paper en salidas, detalles menores). Ningún cambio de
  comportamiento en señales ni en el risk engine aprobando/rechazando —
  los tres fixes afectan a cómo se persiste/lee el estado del paper
  ledger.
  - **Bug 1 — la curva de equity se corrompía con cierres fuera de orden
    cronológico** (`services/execution/paper_ledger.py::close_position` +
    `services/risk/portfolio_state.py::get_latest_equity`): el
    `EquitySnapshot` de un cierre se insertaba con `ts = exit_time` (el
    close de la vela que disparó la salida, potencialmente horas en el
    pasado), pero "última equity" se leía por `ts` más reciente. Si en un
    mismo ciclo se cerraban dos posiciones con `exit_time` no monotónicos
    (p.ej. A tocó SL a las 14:00 y B a las 10:00, procesadas en ese
    orden), el snapshot de B quedaba "detrás" del de A y **su PnL
    desaparecía de la curva de equity** — y con ella del
    `drawdown_killswitch` y del `daily_loss_limit`. Corregido en dos
    frentes: el snapshot usa ahora el tiempo de PROCESO (`ts=now`,
    monotónico; `trade_exits.exit_time` conserva el tiempo de la vela) y
    la lectura de "última equity" ordena por `id` (orden de inserción) en
    vez de por `ts` (`get_latest_equity`, `_get_drawdown_pct`,
    `scripts/estado.py`). `close_position` gana el parámetro `now`.
  - **Bug 2 — la vela EN CURSO entraba en la evaluación de salidas**
    (`services/execution/paper_ledger.py::evaluate_exit`): la ingesta
    upsertea también la kline en formación que devuelve Binance, y
    `update_open_positions` la pasaba a `evaluate_exit` sin filtrar (a
    diferencia del scanner, que filtra con `candles_to_frame`). Dos
    consecuencias: (a) la invalidación técnica usaba un `close` aún no
    definitivo — un dip intra-vela cerraba la posición como
    `closed_invalidated` aunque la vela acabara cerrando por encima del
    nivel, contradiciendo la regla "invalidación por CIERRE" (sección
    7.2); (b) `exit_time` podía quedar **en el futuro** (`close_time`
    reconstruido = `open_time` + duración > `now`), escribiendo
    `trade_exits`/`position_events` con timestamps futuros (y agravando
    el bug 1). Corregido: `evaluate_exit` solo considera velas con
    `close_time <= now` — mismo criterio anti look-ahead que
    `candles_to_frame` (sección 18). SL/TP se siguen evaluando al cierre
    de cada vela (simplificación ya documentada del paper ledger).
  - **Bug 3 — el estado de cartera mezclaba environments**
    (`services/risk/portfolio_state.py`): `get_latest_equity`,
    `_get_open_positions`, `_get_daily_realized_pnl_pct`,
    `_get_equity_peak`, `_get_drawdown_pct` y los dos cooldowns no
    filtraban por `environment`, mientras que el paper ledger escribe
    `environment='paper'` y `estado`/`daily_summary` sí filtran. Una fila
    sembrada por un test de integración (`environment='test'`) — o, en el
    futuro, filas de testnet/live — contaminaba la equity, la exposición
    y los cooldowns que consume el risk engine. Corregido: TODAS las
    queries de `portfolio_state` filtran por `ENVIRONMENT` (constante
    única definida ahí y reutilizada ahora por `paper_ledger`,
    `daily_summary` y `estado` — antes cada módulo declaraba la suya).
    `tests/integration/test_killswitch.py` ya no siembra equity con
    `environment='test'` (era el vector del problema y ya no influye).
  - Tests: 2 casos unitarios nuevos en `test_paper_ledger.py`
    (`test_in_progress_candle_is_ignored`,
    `test_candle_evaluated_once_it_closes`);
    `tests/integration/test_paper_ledger.py` actualizado a la nueva firma
    de `close_position`. **Pendiente de ejecutar la suite en el stack**
    (`docker compose run --rm --no-deps app uv run pytest -v` + la de
    integración): esta ronda se hizo fuera del entorno Docker (revisión
    estática + edición), sin Python 3.12 ni Postgres disponibles.
  - **Limitación documentada (sin fix, README)**: divergencia
    backtest/paper en las SALIDAS — el backtest vectorbt solo modela
    SL/TP (`exits=False`), mientras el paper ledger añade invalidación
    técnica y salida por tiempo. Con `invalidation_level = range_high` y
    entrada por encima de él, la invalidación es en la práctica la salida
    más frecuente y mucho más cercana que el SL: los resultados de
    `backtests/RESULTS.md` no representan la operativa del paper ledger,
    y el `rr_net >= MIN_RR_NET` del risk engine se calcula contra el SL,
    no contra la salida realista. Pendiente de decidir: modelar
    invalidación/tiempo en el backtest o revisar la regla de invalidación.
  - Menores documentados sin fix (README): la vela donde ocurre la
    entrada queda excluida para siempre del seguimiento (hasta 4h sin
    vigilar SL/TP); salidas por gap se registran al precio exacto del SL
    (optimista); solape de 1 vela entre ventanas IS/OOS del walk-forward
    (`df.loc` inclusivo en ambos extremos); frescura de datos solo se
    comprueba sobre 1h (una señal 4h puede nacer de velas 4h obsoletas).

## 2026-07-05

- **[2026-07-05]** **Ingesta de Reddit** (`services/fundamental/
  ingest_reddit.py`, sección 12.2) — completa la ingesta social del
  almacén PIT junto a la RSS/JSON de la ronda anterior:
  - Migración `0005`: tabla `social_items` (sección 12.1) — `platform,
    subreddit, post_id UNIQUE, title, body_text, score_at_fetch,
    num_comments_at_fetch, published_at, fetched_at, raw_jsonb`.
    Idempotente por `post_id` (identificador propio de Reddit, no hace
    falta un `content_hash` calculado como en `news_items`).
  - `core/schemas/fundamental.py::SocialItem` (Pydantic).
  - `services/fundamental/ingest_reddit.py`: mismo patrón que
    `ingest_rss.py` (separación red/parseo, fail-closed por subreddit).
    **Grant OAuth `client_credentials`** (app-only, sin usuario/
    contraseña de Reddit) — solo se necesita lectura pública de r/
    CryptoCurrency + el subreddit de cada activo del universo
    (`ASSET_SUBREDDITS`, mapeo best-effort ya que el documento no los
    nombra), nunca acciones en nombre de una cuenta. Sin
    `REDDIT_CLIENT_ID`/`SECRET` configurados, `ingest_all` no intenta
    nada por red (no es un error).
  - `app/scheduler.py::_fundamental_ingest` ahora corre RSS y Reddit en
    sesiones de DB separadas (si Reddit falla catastróficamente no
    deshace lo que RSS ya confirmó) dentro del mismo job
    `fundamental_ingest_job`.
  - Tests: 2 unitarios nuevos (`test_ingest_reddit.py`: parseo de listado
    contra fixture grabada `tests/fixtures/reddit_listing_sample.json`,
    no-op confirmado sin credenciales — monkeypatch de `httpx.AsyncClient`
    que lanza si se llega a invocar) + 1 de integración (idempotencia de
    `persist_social_items`). 68/68 unitarios + 6/6 integración en verde,
    `mypy --strict` limpio (23 archivos).
  - **Bloqueante externo, no de código**: el registro de la app de Reddit
    del usuario quedó atascado en el propio proceso de verificación de
    desarrollador de Reddit ("Responsible Builder Policy" + registro de
    API aparte) — fuera de nuestro control. El código queda listo y
    verificado (no-op limpio); solo falta que el usuario complete ese
    registro en reddit.com y rellene `REDDIT_CLIENT_ID`/`SECRET` en `.env`.
  - `# DECISION` nuevo en `ESPECIFICACION_SISTEMA_TRADING.md` sección
    12.2 (grant OAuth elegido + mapeo de subreddits). `README.md` y
    `docs/PHASE_2_REPORT.md` actualizados.

## 2026-07-03

- **[2026-07-03, tercera ronda]** **Conectividad Ollama + variables de
  Reddit** — prepara el terreno para seguir fase 2 (clasificador +
  ingesta social) sin construirlos todavía:
  - `app/config.py`: `ollama_host` (nuevo, `http://host.docker.internal:
    11434`) y `ollama_model` default cambiado a `qwen3.5:9b`. El
    documento no especifica ni la conectividad ni un modelo obligatorio
    (solo ejemplos: "llama3.1:8b o qwen2.5:7b") — decisión del usuario:
    conectar al Ollama de SU HOST (ya tiene modelos descargados, evita
    duplicar dentro de Docker) en vez de añadir un servicio `ollama` a
    docker-compose. Modelo elegido tras comparar lo ya disponible en el
    host (`deepseek-r1:14b`, `qwen3.5:9b`): se descartó `deepseek-r1:14b`
    por ser un modelo de razonamiento (más lento, pensado para
    matemáticas/lógica, no para clasificación simple y frecuente cada 15
    min) — `qwen3.5:9b` es generación más nueva que el `qwen2.5:7b` de
    ejemplo del documento y ya estaba descargado.
  - `docker-compose.yml`: `extra_hosts: host.docker.internal:host-gateway`
    en el servicio `app` — `host.docker.internal` ya funciona solo en
    Docker Desktop (Windows/Mac); esto lo hace portable también en Linux.
  - `app/config.py` + `.env`/`.env.example`: `REDDIT_CLIENT_ID`/
    `REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` (nuevos, vacíos) —
    variables no listadas en el documento original para la sección 12.2;
    pendiente que el usuario cree la app en reddit.com/prefs/apps
    (tipo "script") y las rellene.
  - `# DECISION` nuevo en `ESPECIFICACION_SISTEMA_TRADING.md` Apéndice A
    documentando ambas decisiones (conectividad Ollama + variables Reddit).
  - Verificado real: `docker compose exec app curl
    http://host.docker.internal:11434/api/tags` responde 200 con los
    modelos del host visibles desde dentro del contenedor. 66/66 tests
    unitarios + 5/5 integración en verde, `mypy --strict` limpio — sin
    regresiones (cambio de config puro, sin lógica nueva).

- **[2026-07-03, segunda ronda]** **Alertas de Telegram** + **arranque de
  Fase 2** (capa fundamental). Decisión del usuario: pausar el resto de
  fase 1 (executor OCO real, bloqueado por credenciales de testnet) para
  primero tener Telegram funcionando y luego arrancar fase 2, en vez de
  saltar a fase 3 (que depende de fase 2 — el meta-decider fusiona
  técnico × fundamental).
  - `notifications/telegram.py` (nuevo): `send_message(settings, text)`
    vía `httpx` contra la API HTTP de Telegram. **Fail-open** por diseño
    (a diferencia del resto del sistema): una excepción de red se loguea
    y no se propaga — un fallo de Telegram nunca debe tumbar un ciclo del
    scheduler ni bloquear una decisión de trading. No-op si
    `TELEGRAM_BOT_TOKEN`/`CHAT_ID` están vacíos.
  - Cuatro disparadores conectados: nueva posición de papel y cierre con
    PnL (`services/execution/paper_ledger.py::open_position/
    close_position`, ganan parámetro `settings`), halt/rearme
    (`scripts/halt.py`/`scripts/rearm.py`), y un resumen diario nuevo.
  - `services/reporting/daily_summary.py` (nuevo): agrega equity/drawdown
    (reutiliza `portfolio_state.build_portfolio_snapshot`), trades del día
    (`trade_exits`), rechazos por motivo del día (`unnest` sobre
    `decision_logs.rejection_reasons`) y estado de `CORE_ASSETS` aunque no
    haya setup (sección 21.4) — reutiliza `services/scanner/
    regime.py::compute_btc_regime` tal cual (ya es agnóstica al activo)
    sobre las velas 4h ya ingeridas de cada core asset, sin escribir en
    `regime_log` (esa tabla es específicamente el régimen de BTC que
    consume el risk engine).
  - `app/scheduler.py` gana dos jobs nuevos e independientes de
    `market_cycle_job` (dominios de fallo separados): `fundamental_
    ingest_job` (misma cadencia de 15 min) y `daily_summary_job` (cron
    22:00 UTC).
  - **Fase 2 — arranque**: `news_items` (migración `0004`, solo esta
    tabla — `social_items`/`item_classifications`/`classifier_scorecard`
    quedan fuera por ahora, sin consumidor todavía). `core/schemas/
    fundamental.py::NewsItem`. `services/fundamental/ingest_rss.py`:
    ingesta RSS (CoinDesk, The Block vía `feedparser`) + el endpoint JSON
    no documentado de anuncios de Binance (sección 12.2), separando
    fetch (red) de parseo (puro, testeable con fixtures) igual que
    `services/data/binance_market_data.py`. `extract_asset_tags`:
    heurística determinista de palabra completa contra alias conocidos
    del universo — filtra relevancia por activo, NO es la clasificación
    real (eso lo hará Ollama, todavía sin construir). Cada fuente falla
    de forma independiente (`ingest_all`, fail-closed por fuente).
  - **Explícitamente diferido** (ver `docs/PHASE_2_REPORT.md`): Reddit
    (necesita registrar una app OAuth — credenciales que el proyecto no
    tiene) y clasificador Ollama (necesita decidir conectividad:
    docker-compose vs `host.docker.internal`, sin resolver).
  - Nuevas dependencias `httpx`/`feedparser`, justificadas en README
    (sección 20 regla 6). `mypy --strict` ampliado a `notifications/`,
    `services/fundamental/`, `services/reporting/` — 22 archivos, sin
    errores.
  - 9 tests unitarios nuevos (`test_telegram.py`: no-op sin credenciales,
    payload correcto, fail-open ante error de red; `test_ingest_rss.py`:
    `extract_asset_tags` con casos límite de frontera de palabra,
    `content_hash` determinista, parseo de RSS/JSON contra fixtures
    grabadas) + 3 tests de integración nuevos (`test_ingest_rss.py`:
    idempotencia de `persist_news_items`; `test_daily_summary.py`:
    agregación de rechazos/trades — en deltas, no valores absolutos, ver
    abajo). 66/66 unitarios + 5/5 integración en verde.
  - **Tres bugs de test encontrados durante la verificación, los tres por
    la misma causa raíz**: el scheduler en vivo llevaba corriendo toda la
    sesión y de hecho **abrió una posición de papel real** (`ETHUSDT`) —
    primera entrada real generada de forma 100% automática — **y más
    tarde la cerró también sola** (`closed_sl`, pnl −6.6444 USDT,
    confirmado con `scripts.estado`). Eso rompió tres tests que asumían
    una DB "limpia" o fechas fijas en el pasado: (1) `test_killswitch.py`
    afirmaba `verdict.approved is True` antes de halt, pero la exposición
    real de esa posición hace que `correlated_exposure` legítimamente
    falle — corregido para afirmar solo sobre `checks["system_not_halted"]`
    (lo único que ese test ejercita) en vez del `approved` global. (2)
    `test_daily_summary.py` afirmaba conteos/pnl absolutos
    (`"liquidity: 2"`, `"50.0000"`) contra tablas que la app real sigue
    escribiendo en paralelo — corregido para afirmar sobre el delta
    introducido por los datos sembrados. (3) `test_paper_ledger.py`
    (sesión anterior) anclaba sus timestamps a una fecha fija en el
    pasado (2026-01-01): en cuanto existió una operación de papel REAL
    con timestamp posterior, esa fecha dejó de ser "la última equity" y
    las aserciones de equity empezaron a leer el dato real en vez del
    propio del test — corregido anclando todos los timestamps del test a
    `datetime.now(tz=UTC)` con offsets relativos (`+3h`), que por
    construcción siempre quedan por delante de cualquier dato real
    existente. Lección general: cualquier test de integración que escriba
    en `equity_snapshots` (sin FK, "última fila por `ts`" global) debe
    anclarse a `now()` real, nunca a una fecha fija — confirmado con una
    segunda corrida repetida de la suite de integración tras el fix.
  - Verificado end-to-end con el stack completo: migración `0004`
    aplicada limpia; `fundamental_ingest_job` corrió en vivo contra las 3
    fuentes reales (`news_items`: 25 CoinDesk + 20 The Block + 20 Binance
    anuncios); halt y rearme reales confirmados recibidos en el Telegram
    real del usuario.
  - `# DECISION` nuevo en `ESPECIFICACION_SISTEMA_TRADING.md` sección
    12.2 (alcance de este arranque + uso del endpoint no documentado de
    Binance). `README.md` con secciones de Telegram y capa fundamental.
    `docs/PHASE_2_REPORT.md` (nuevo).

- **[2026-07-03]** **Paper ledger interno** (`services/execution/
  paper_ledger.py`) — sustituto temporal del executor OCO real mientras no
  existan credenciales de testnet, para poder ver rentabilidad forward de
  las señales del sistema sin ninguna credencial:
  - Cuando el risk engine aprueba una entrada (`would_enter_no_executor`),
    `run_scan_cycle` y `/analiza <PAR> operar` abren ahora una posición de
    **papel** (`environment="paper"`, sin llamar a ningún exchange) y
    registran `final_action=enter` en vez de forzar `watchlist`.
  - La posición se sigue vela a vela (velas reales ya ingeridas) hasta
    tocar SL, TP, invalidarse técnicamente (`invalidation_level`, nuevo
    campo estructurado en `TechnicalSignal`, antes solo existía el string
    legible `invalidation_rule`) o expirar por horizonte de tiempo
    (`MAX_HOLD_HOURS_INTRADAY`/`MAX_HOLD_DAYS_SWING`, primer uso real de
    estos parámetros). Criterio conservador: SL se comprueba antes que TP
    si ambos se tocan en la misma vela.
  - Escribe `trade_entries`/`trade_exits`/`equity_snapshots`/
    `position_events` con la misma forma que usaría un executor real —
    `services/risk/portfolio_state.py` no cambia nada y los checks de
    cartera (`max_positions`, cooldowns, `daily_loss_limit`,
    `drawdown_killswitch`) se activan solos en cuanto hay posiciones de
    papel abiertas/cerradas.
  - `app/scheduler.py::market_cycle_job` gana un tercer paso
    (`_update_paper_positions`) tras el scan, en la misma cadencia de 15
    min — simplificación documentada frente a los 5 min de la sección 11
    original (pensada para el monitor real contra testnet).
  - Migración `0003`: `trade_entries` gana `asset`, `timeframe`,
    `horizon_class`, `invalidation_level` (el schema original solo tenía
    `decision_log_id` para llegar al activo vía join; con el monitor
    corriendo cada ciclo sobre posiciones abiertas, tenerlo directo en la
    fila evita un join en cada chequeo).
  - Pequeño refactor en `services/risk/portfolio_state.py`: `_get_equity_
    quote` pasa a ser pública (`get_latest_equity`) y se extrae
    `_get_equity_peak`/`compute_drawdown_pct`, reutilizados por el paper
    ledger al cerrar una posición — mismo cálculo de drawdown que el risk
    engine, sin duplicarlo.
  - `scripts/estado.py` (nuevo, equivalente CLI de `/estado`, sección 21 —
    hoy solo speced para Telegram, bloqueado por credenciales): sistema,
    régimen BTC, equity y drawdown actuales, posiciones de papel abiertas,
    y resumen de rentabilidad de las cerradas (win rate, pnl total, pnl%
    medio, profit factor).
  - Tests: 9 casos unitarios de `evaluate_exit` (SL, TP, SL-gana-a-TP en la
    misma vela, invalidación, sin salida, anti look-ahead, salida por
    tiempo en sus dos variantes) + 2 tests de integración nuevos contra
    Postgres real (`tests/integration/test_paper_ledger.py`): apertura +
    cierre directo, y `update_open_positions` cerrando por velas reales
    insertadas a mano. 57/57 tests unitarios + 3/3 integración en verde,
    `mypy --strict` limpio (`services/execution` añadido al scope de
    mypy en `pyproject.toml`).
  - **Bug de infraestructura de tests encontrado y corregido**: el engine
    async de `db/session.py` es un singleton a nivel de módulo, pero
    pytest-asyncio (config por defecto) crea un event loop nuevo por test
    — al reutilizar conexiones pooladas de un loop en otro, asyncpg
    revienta (`Future attached to a different loop`). Corregido fijando
    `asyncio_default_fixture_loop_scope`/`asyncio_default_test_loop_scope
    = "session"` en `pyproject.toml`.
  - **Bug propio encontrado y corregido en el mismo desarrollo**: los
    primeros tests de integración no limpiaban las filas de
    `equity_snapshots` que generaban (para no arriesgarse a borrar datos
    reales de otro entorno) — pero al no existir todavía ningún cierre
    real de paper trading, esas filas de test (fechadas en 2026-01-01)
    quedaban como "la última equity conocida" y corrompían `/estado`.
    Corregido acotando el borrado por `ts < TEST_EQUITY_CUTOFF` (rango de
    fechas exclusivo de los fixtures, muy anterior a cualquier operación
    real) y purgadas las filas ya escritas en la DB de desarrollo.
  - `# DECISION` nuevo en `ESPECIFICACION_SISTEMA_TRADING.md` sección
    10.1: documenta esta sustitución temporal del `paper_ledger` real
    (fills de testnet) por la simulación interna.
  - Verificado con el stack completo: `docker compose up -d --build`
    (migración 0003 aplicada limpia), varios ciclos automáticos completos
    (ingesta → scan → paper positions) sin errores, `/analiza <PAR>
    operar` probado contra 9 pares del universo (sin setup real disponible
    en el momento de la verificación — igual que sesiones anteriores,
    confirma que el camino de apertura solo se activa con una aprobación
    real del risk engine) y `/estado` mostrando el estado correcto.

## 2026-07-02

- **[2026-07-02 12:27]** Añadido `ESPECIFICACION_SISTEMA_TRADING.md`: documento
  de especificación completo del sistema multiagente de trading (crypto spot,
  swing corto) — fases, principios de diseño, contratos de datos, risk
  engine, ejecución, backtesting y gates para capital real.

- **[2026-07-02 13:03]** Completada **Fase 0 — Infraestructura mínima (sin
  trading)**:
  - Scaffold del repo: `pyproject.toml` (uv), `Dockerfile` multi-stage,
    `docker-compose.yml` (`postgres` + `app`), `.env.example`, `.gitignore`,
    `.dockerignore`.
  - `app/config.py`: `Settings` (Pydantic Settings) con todos los parámetros
    del Apéndice A del documento y sus defaults.
  - `core/logging.py`: logging JSON estructurado a stdout (`structlog`).
  - `core/schemas/market.py`: contratos Pydantic `Candle` y `MarketSnapshot`
    (Decimal en todo precio/cantidad, nunca float).
  - `db/models.py` + Alembic (`db/migrations/`): tablas `assets`, `candles`
    (PK compuesta `asset, timeframe, open_time`) y `market_snapshots`.
  - `services/data/binance_market_data.py`: cliente de solo lectura contra
    `https://api.binance.com` (producción) para klines 1h/4h y ticker 24h.
    Decisión: los datos de mercado siempre vienen de producción,
    independientemente de `ENVIRONMENT` (sección 10.1 del documento).
  - `services/data/persistence.py`: upsert idempotente de assets/velas,
    insert append-only de snapshots.
  - `app/scheduler.py`: APScheduler in-process, job de ingesta cada
    `SCAN_INTERVAL_MINUTES` (default 15 min) para el universo de 10 pares.
  - `app/main.py`: FastAPI con lifespan (arranca/para el scheduler) y
    `/health` (estado DB, frescura de datos, modo, environment, `git_sha`).
  - Tests unitarios: `test_config.py`, `test_market_schema.py`,
    `test_binance_market_data.py` (con fixtures grabadas de Binance) — 6/6
    en verde.
  - **Bug encontrado y corregido**: columnas `open_time`/`ts` en el ORM sin
    `DateTime(timezone=True)` explícito rompían la ingesta real contra
    Postgres (`asyncpg.exceptions.DataError` por mezclar datetimes aware/naive).
  - **Verificación end-to-end real**: `docker compose up -d --build` levantó
    postgres + app, Alembic aplicó la migración inicial, el scheduler
    ingestó datos reales de Binance: 10 assets, 5000 velas 1h + 5000 velas
    4h (500 por par × 10 pares) y 10 snapshots de ticker 24h. `/health`
    respondió `db_ok: true, data_fresh: true`.
  - `README.md` y `docs/PHASE_0_REPORT.md` con instrucciones de arranque,
    qué se hizo, qué lo cubre y qué queda fuera.
  - `git init` + commit inicial (`5f15ecb`) con los 35 archivos del scaffold.

- **[2026-07-02 15:42]** **Fase 1 en progreso** — pipeline técnico + risk
  engine + `/analiza` funcionando end-to-end contra Binance real (falta
  executor OCO, monitor, backtesting y Telegram):
  - `core/enums.py` completo (sección 7.1 + `Trigger` de 21.1), con dos
    adiciones documentadas (`no_setup`, `sl_distance_invalid`).
  - `core/schemas/technical.py`, `risk.py`, `decision.py`: `TechnicalSignal`,
    `RiskVerdict`, `DecisionRecord`.
  - `services/technical/indicators.py`: EMA, ATR, RSI, volumen relativo,
    rango rodante (cálculo propio), `candles_to_frame` con filtro anti
    look-ahead explícito.
  - `services/scanner/regime.py`: régimen BTC 4h (EMA50/200 + pendiente +
    percentil 90 histórico de ATR%).
  - `services/scanner/filters.py`: los 4 filtros duros de la sección 8.2.
  - `services/technical/setups.py` + `signal_builder.py`: detección de
    ruptura de rango con confirmación de volumen y construcción de
    `TechnicalSignal` (SL/TP por ATR, R:R bruto ≥ 2.0).
  - `services/risk/engine.py` + `sizing.py` + `portfolio_state.py`: los 5
    checks por operación + 7 de cartera de la sección 9, sizing por riesgo
    fijo fraccional. Nuevas tablas (migración `0002`): `regime_log`,
    `decision_logs`, `trade_entries`, `trade_exits`, `position_events`,
    `equity_snapshots`, `system_state`.
  - `journal/decision_logger.py`: persiste `decision_logs` siempre.
  - `scripts/analiza.py`: equivalente CLI de `/analiza <PAR> [operar]`
    (sección 21.2) — universo abierto con descarga on-demand (21.3),
    límite `MANUAL_MAX_PER_HOUR`, rechazo de stablecoins (21.5). Modo
    "operar" no ejecuta órdenes reales todavía (executor no implementado);
    lo indica explícitamente en el informe si el risk engine aprobaría.
  - 45 tests unitarios en verde, incluidos 14 casos parametrizados del
    risk engine (uno por check, pasa/falla en aislamiento) y test anti
    look-ahead. `mypy --strict` sin errores en `core/` y `services/risk/`.
  - **Verificado con datos reales**: `SOLUSDT` y `ETHUSDT` detectaron
    rupturas de rango reales en Binance y fueron rechazadas correctamente
    por `sl_distance_max`; `LTCUSDT` (fuera de universo) se descargó
    on-demand y se rechazó por `liquidity`; `USDCUSDT` se rechazó como
    stablecoin sin generar análisis. Filas confirmadas en `decision_logs`
    y `regime_log` vía `psql`.
  - Documento de especificación actualizado con 6 decisiones de diseño
    (`# DECISION`) donde el documento original tenía huecos: dos
    `RejectionReason` nuevos, dos parámetros de config nuevos
    (`RANGE_LOOKBACK_CANDLES`, `VOLUME_CONFIRM_MULT`), un bootstrap de
    equity (`PAPER_STARTING_EQUITY_USDT`), y la tabla `system_state`.
  - `docs/PHASE_1_REPORT.md` con el detalle completo, incluido un hallazgo
    a validar en backtesting: `sl_distance_max` (4×ATR14) rechaza la
    mayoría de rupturas reales detectadas con `RANGE_LOOKBACK_CANDLES=20`.

- **[2026-07-02 16:55]** Segunda ronda de verificación de Fase 1 (arranque
  limpio desde cero, sin reusar contenedores de la sesión anterior):
  - `docker compose up -d --build` con volumen de Postgres nuevo: las
    migraciones `0001` y `0002` aplican limpias en orden, ingesta de
    mercado corre en el primer ciclo del scheduler, `/health` responde
    `data_fresh: true`.
  - 45/45 tests unitarios en verde y `mypy --strict` sin errores en
    `core/` y `services/risk/` (confirmado de nuevo tras la primera
    verificación de la sesión anterior).
  - `/analiza BTCUSDT` (informe): otra ruptura real detectada en 1h,
    rechazada de nuevo por `sl_distance_max` — confirma el hallazgo
    anterior con un tercer activo (SOL, ETH, BTC ya lo han mostrado).
  - `/analiza DOGEUSDT operar`: rechazado en filtros duros por
    `liquidity` con `final_action=reject` (no `watchlist`, correcto para
    modo operar) — confirma que el par SÍ está en `UNIVERSE` pero no pasa
    el filtro de liquidez en este momento del mercado.
  - 3 `decision_logs` nuevos verificados por `psql` (ids 1-3 de la DB
    limpia: ETHUSDT, BTCUSDT, DOGEUSDT), todos con `trigger=manual`.
  - Sin cambios de código en esta ronda — es una verificación de
    regresión, no una implementación nueva.

- **[2026-07-02 18:53]** Scanner automático + backtesting walk-forward +
  killswitch probado (siguen bloqueados por credenciales: executor OCO,
  paper ledger, reconciliación, Telegram):
  - **Refactor sin duplicación**: extraída la lógica compartida de
    `/analiza` a `services/scanner/scanner.py` (`evaluate_asset`,
    `evaluate_regime`, `decide_final_action`, `run_scan_cycle`).
    `scripts/analiza.py` ahora la reutiliza en vez de duplicarla.
  - `app/scheduler.py`: el ciclo automático (`market_cycle_job`) ahora
    encadena ingesta + `run_scan_cycle` (`trigger=scheduled`) sobre todo
    `UNIVERSE`, leyendo los datos recién ingestados de DB (sin llamadas
    extra a Binance salvo `exchangeInfo` cuando hay setup real).
  - **Verificado con datos reales**: ciclo automático aprobó un setup
    real sin intervención manual (`XRPUSDT`, `rr_net_of_fees≈1.91`,
    `final_action=watchlist` con aviso de executor pendiente); los otros
    9 activos del universo quedaron correctamente rechazados con motivos
    variados. 10 `decision_logs` con `trigger=scheduled` confirmados por
    `psql`.
  - `scripts/halt.py` / `scripts/rearm.py`: halt/rearme manual del
    killswitch (sección 9.2/15), persistido en `system_state`. `/health`
    reporta el `system_state` real de DB en vez de un valor fijo.
  - `tests/integration/test_killswitch.py` **(nuevo, contra Postgres
    real)**: confirma que halt bloquea el risk engine y rearme lo
    desbloquea, sin recuperación automática.
  - `backtests/download_history.py`: descarga paginada de histórico
    (Binance público) — 800 días (2024-04-23 → 2026-07-02), 19200 velas
    1h + 4800 velas 4h por cada uno de los 10 pares del universo.
  - **Bug corregido en `services/data/persistence.py`**: `upsert_candles`
    fallaba con lotes grandes (`asyncpg.exceptions...: cannot exceed
    32767` parámetros) al insertar 19200 filas de una vez; corregido con
    batching de 2000 filas.
  - `backtests/strategy_breakout.py` + `walk_forward.py`: señales
    vectorizadas reutilizando `compute_breakout_frame` (refactor de
    `services/technical/setups.py` para compartir la MISMA lógica entre
    scanner en vivo y backtest). Walk-forward sin solape (in-sample 6m /
    out-of-sample 2m), grid search de `RANGE_LOOKBACK_CANDLES` ×
    `VOLUME_CONFIRM_MULT` por ventana in-sample.
  - **Bug metodológico encontrado y corregido**: la primera versión del
    walk-forward reportaba un retorno compuesto de **+408.198%** —
    vectorbt da el retorno del INSTRUMENTO (asumiendo ~100% del capital),
    no del equity real gestionado con `RISK_PER_TRADE=0.5%`. Corregido
    reescalando cada trade a `equity_impact = return_instrumento ×
    (RISK_PER_TRADE / sl_pct_del_trade)`; el retorno baja a un +183.5%
    (todavía optimista por la simplificación de curva secuencial, pero
    ya no fantasioso).
  - `backtests/RESULTS.md`: resultados reales — 60 folds walk-forward,
    322 trades out-of-sample, win rate 55.6%, expectancy **positiva**
    (+0.327% de equity/trade neto de fees), profit factor 2.45, max
    drawdown −8.87% (bajo el killswitch del 10%). Comparado contra
    buy&hold BTC (−7.68% mismo periodo) y contra 0%. Limitaciones
    documentadas explícitamente (histórico corto, curva secuencial no
    multi-posición, slippage aproximado, timeframes testeados por
    separado, universo pequeño y correlacionado).
  - `tests/unit/test_backtest_regression.py` **(nuevo)**: fixture fijo de
    velas → entrada/SL/TP/trade/retorno EXACTOS conocidos (protege contra
    divergencia silenciosa entre `services/technical/` y `backtests/`).
  - 48/48 tests unitarios + 1/1 test de integración en verde, `mypy
    --strict` sin errores en `core/` y `services/risk/`.
  - `README.md` y `docs/PHASE_1_REPORT.md` actualizados con todo lo
    anterior; sigue documentado como bloqueante real para cerrar Fase 1
    la falta de `BINANCE_API_KEY`/`SECRET` de testnet y
    `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
