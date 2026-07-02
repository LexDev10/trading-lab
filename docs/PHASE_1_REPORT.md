# Fase 1 — Pipeline técnico + paper trading

Estado: **en progreso, no cerrada**. Completado y verificado dos veces
(arranque limpio, sin reusar estado) en sesiones distintas: contratos,
indicadores, régimen, filtros duros, detección de setups, risk engine
completo, journal, y `/analiza` (CLI) funcionando end-to-end contra
Binance real.

**Bloqueante real para cerrar la fase** (no es trabajo pendiente de
escribir código, es dependencia externa): el executor OCO contra Binance
Spot Testnet y las alertas de Telegram necesitan credenciales que este
proyecto no tiene todavía —`BINANCE_API_KEY`/`SECRET` de testnet y
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`—. Sin eso no hay manera honesta de
verificar "una posición completa abre y cierra en testnet vía OCO" ni
"alertas Telegram funcionan" (criterios de aceptación, sección 19), así
que construir esas piezas a ciegas violaría el principio de este proyecto
de verificar todo contra el sistema real antes de darlo por bueno.

## Qué se hizo

- `core/enums.py`: todos los enums de la sección 7.1, más `Trigger`
  (sección 21.1) y dos adiciones documentadas (`no_setup`,
  `sl_distance_invalid` — ver DECISION en el propio documento de
  especificación, sección 7.1).
- `core/schemas/technical.py`, `risk.py`, `decision.py`: `TechnicalSignal`,
  `RiskVerdict`, `DecisionRecord` tal como los define la sección 7.
- `services/technical/indicators.py`: EMA, ATR (Wilder), RSI, volumen
  relativo, rango rodante — cálculo propio en pandas/numpy, sin
  dependencias pesadas. `candles_to_frame` descarta explícitamente
  cualquier vela cuyo `close_time` sea posterior a "ahora" (anti
  look-ahead).
- `services/scanner/regime.py`: régimen de BTC en 4h (EMA50/EMA200 +
  pendiente + percentil 90 histórico de ATR%), sección 8.3.
- `services/scanner/filters.py`: los 4 filtros duros de la sección 8.2,
  evaluados sin cortocircuitar.
- `services/technical/setups.py` + `signal_builder.py`: detección de
  ruptura de rango con confirmación de volumen (hipótesis de la sección
  3.1) y construcción de `TechnicalSignal` (SL = mínimo del rango −
  0.5×ATR14, TP con R:R bruto ≥ 2.0). Conversión a `Decimal` solo aquí
  (los indicadores internos usan float, ver DECISION en el código).
- `services/risk/engine.py`, `sizing.py`, `portfolio_state.py`: los 5
  checks por operación + 7 checks de cartera de la sección 9, sizing por
  riesgo fijo fraccional (0.5% default), y `PortfolioSnapshot` construido
  desde `trade_entries`/`trade_exits`/`equity_snapshots`/`system_state`
  (nuevas tablas, migración `0002`).
- `journal/decision_logger.py`: persiste `DecisionRecord` en
  `decision_logs` siempre, acepte o rechace.
- `scripts/analiza.py`: equivalente CLI de `/analiza <PAR> [operar]`
  (sección 21.2) — pipeline completo, universo abierto (descarga
  on-demand fuera de `UNIVERSE`, sección 21.3), límite de
  `MANUAL_MAX_PER_HOUR`, rechazo explícito de stablecoins (sección 21.5).
  El modo "operar" respeta el risk engine y el filtro de régimen sin
  ningún override, pero en este build no envía órdenes reales porque el
  executor todavía no existe — se avisa explícitamente en el informe si
  el risk engine habría aprobado.

## Verificación end-to-end (real, no simulada)

`docker compose up -d --build` con DB limpia → migraciones `0001` y
`0002` aplican correctamente → scheduler ingesta el universo real →
`docker compose exec app uv run python -m scripts.analiza <PAR>` corre
contra klines/ticker/exchangeInfo reales de Binance producción:

- `SOLUSDT` (informe): detectó una ruptura de rango real en 4h,
  `rr_net_of_fees≈1.96`, rechazada por `sl_distance_invalid`
  (`sl_distance_max`). `decision_log` persistido.
- `SOLUSDT operar`: mismo resultado, `final_action=reject` (en vez de
  `watchlist`, correcto para modo operar no aprobado).
- `LTCUSDT` (fuera de `UNIVERSE`): descarga on-demand ejecutada,
  rechazado en filtros duros por `liquidity` — comportamiento correcto
  de la sección 21.3.
- `USDCUSDT`: rechazado con mensaje explicativo, sin análisis ni
  `decision_log` (sección 21.5).

`docker compose exec postgres psql ...` confirmó las filas reales en
`decision_logs` y `regime_log`.

### Segunda verificación (regresión, sesión posterior)

Repetido desde cero (`docker compose down -v` → `up -d --build`, volumen
de Postgres nuevo) sin cambios de código, para confirmar que no hay
estado oculto ni regresiones:

- Migraciones `0001`+`0002` limpias, ingesta OK, `/health` con
  `data_fresh: true`.
- 45/45 tests + `mypy --strict` limpio, igual que la primera vez.
- `BTCUSDT` (informe): ruptura real en 1h, rechazada de nuevo por
  `sl_distance_max` — tercer activo distinto (tras SOL y ETH) que golpea
  el mismo check, refuerza el hallazgo de la sección siguiente.
- `DOGEUSDT operar`: dentro de `UNIVERSE`, rechazado por `liquidity` en
  ese momento del mercado, `final_action=reject` correctamente.
- 3 `decision_logs` nuevos confirmados por `psql` en la DB limpia.

## Tests

45 tests unitarios en verde:

- `test_indicators.py`: EMA/ATR/RSI/volumen relativo/rango rodante,
  propiedades conocidas (constante, monotonía, no-negatividad, límites).
- `test_regime.py`: `trend_up`, `trend_down`, `chop_high_vol`, e
  histórico insuficiente (fail-closed a `range`).
- `test_filters.py`: cada uno de los 4 filtros duros, pasa y falla.
- `test_setups.py`: ruptura detectada, ruptura sin confirmación de
  volumen, sin ruptura, histórico insuficiente.
- `test_risk_engine.py`: **caso base con todos los checks en verde +
  un caso por cada uno de los 14 checks fallando en aislamiento**
  (sección 18: "uno que pasa y uno que falla por check"), verificando
  además que ningún otro check se ve afectado.
- `test_anti_lookahead.py`: una vela en curso (close_time futuro) se
  descarta; velas cerradas se conservan.

`mypy --strict` sobre `core/` y `services/risk/`: **sin errores** (13
archivos).

## Decisiones de diseño documentadas (añadidas al documento de spec)

Todas marcadas `# DECISION` en el código y reflejadas en
`ESPECIFICACION_SISTEMA_TRADING.md`:

1. `RejectionReason.no_setup` — el filtro de "movimiento" no tenía motivo
   de rechazo propio.
2. `RejectionReason.sl_distance_invalid` — los checks `sl_distance_min`/
   `max` no tenían motivo de rechazo propio; `notional_min` reutiliza
   `exchange_filter`.
3. `RANGE_LOOKBACK_CANDLES` (20) y `VOLUME_CONFIRM_MULT` (1.5) — nuevos
   parámetros de config, necesarios para el detector de ruptura, no
   listados originalmente en el Apéndice A.
4. `PAPER_STARTING_EQUITY_USDT` (10000) — equity de arranque del risk
   engine mientras no exista ningún `equity_snapshots` real (antes del
   primer fill en testnet); se sustituye por completo en cuanto exista
   histórico real.
5. Tabla `system_state` (no listada en la sección 16) — necesaria para
   persistir el killswitch/halt a través de reinicios.
6. Clasificación de `conviction` (`strong`/`moderate`) por umbral de
   `rel_volume` — el documento delega el calibrado exacto a fase 1-2 con
   datos reales (sección 13); la regla actual es conservadora y explícita
   en `signal_builder.py`.

## Hallazgo relevante (a validar en backtesting)

En las pruebas manuales, `sl_distance_max` (distancia entrada→SL ≤
4×ATR14) fue el check que más rechazó setups reales — confirmado ahora
con tres activos distintos en dos sesiones (SOLUSDT, ETHUSDT, BTCUSDT).
Con `RANGE_LOOKBACK_CANDLES=20`, el rango roto suele ser más ancho que
4×ATR antes incluso de restar el buffer de 0.5×ATR del stop. No se ha
tocado el umbral (es un default explícito del documento, sección 9.1);
esto es exactamente el tipo de calibración que la sección 13 reserva
para el backtesting walk-forward de fase 1, no para ajustar a mano.

## Qué quedó fuera (pendiente de esta fase)

Bloqueado por credenciales que el proyecto no tiene todavía (no es falta
de tiempo, es que no se puede verificar honestamente sin ellas):

- `services/execution/binance_executor.py` (OCO en testnet, idempotencia,
  fills parciales, rate limits) — sección 10. Necesita
  `BINANCE_API_KEY`/`SECRET` de testnet.
- `services/monitor/` (position_monitor, exit_rules) — sección 11.
  Depende de que existan posiciones reales abiertas por el executor.
- `services/execution/paper_ledger.py` y `equity_snapshots` reales (hoy
  usa el bootstrap `PAPER_STARTING_EQUITY_USDT`). Depende del executor.
- `notifications/telegram.py` y comando `/estado`. Necesita
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
- Reconciliación (`services/execution/reconciler.py`). Depende del
  executor y de las keys de testnet.

Sin bloqueo externo, pendiente por priorización (siguiente en la cola):

- `backtests/` con vectorbt y walk-forward — sección 14. Solo necesita
  velas históricas (endpoint público), no requiere credenciales.
- Enganchar el scanner (ciclo automático sobre `UNIVERSE`) al
  `app/scheduler.py` — hoy el único disparador activo del pipeline
  técnico+risk es `trigger=manual` vía `/analiza`. Tampoco requiere
  credenciales (modo informe, no ejecuta).

## Cómo reproducir la verificación

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec app uv run python -m scripts.analiza SOLUSDT
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
docker compose run --rm --no-deps app uv run pytest -v
docker compose run --rm --no-deps app uv run mypy
docker compose down
```
