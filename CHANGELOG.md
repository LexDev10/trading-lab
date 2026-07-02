# Changelog

Registro cronológico de lo implementado en el proyecto. Formato:
`[YYYY-MM-DD HH:MM] Descripción`.

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
