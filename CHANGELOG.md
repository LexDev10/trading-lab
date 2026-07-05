# Changelog

Registro cronológico de lo implementado en el proyecto. Formato:
`[YYYY-MM-DD HH:MM] Descripción`.

## 2026-07-03

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
