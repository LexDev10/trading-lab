# Fase 1 — Pipeline técnico + paper trading

Estado: **en progreso, no cerrada — pausada deliberadamente** (decisión
del usuario, 2026-07-03: priorizar Telegram + arrancar fase 2 en vez de
seguir esperando credenciales de testnet; ver
[docs/PHASE_2_REPORT.md](PHASE_2_REPORT.md)). Lo único que falta para
cerrarla (executor OCO real, reconciliación) sigue bloqueado por
`BINANCE_API_KEY`/`SECRET` de testnet, no por trabajo pendiente. Todo lo
que se puede construir y
verificar honestamente SIN credenciales externas está hecho: contratos,
indicadores, régimen, filtros duros, detección de setups, risk engine
completo, journal, `/analiza` y `/estado` (CLI), **scanner automático
enganchado al scheduler**, **backtesting walk-forward con resultados
reales**, **killswitch con halt/rearme manual probado por integración**, y
un **paper ledger interno** que abre/sigue/cierra posiciones simuladas
sobre velas reales (sin exchange) cuando el risk engine aprueba — permite
ver la rentabilidad forward de las señales sin ninguna credencial.

**Bloqueante real para terminar de cerrar la fase** (no es trabajo
pendiente de escribir código, es dependencia externa): el executor OCO
real contra Binance Spot Testnet, la reconciliación y las alertas de
Telegram necesitan credenciales que este proyecto no tiene todavía —
`BINANCE_API_KEY`/`SECRET` de testnet y
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. Sin eso no hay manera honesta de
verificar "una posición completa abre y cierra en testnet vía OCO" ni
"alertas Telegram funcionan" (criterios de aceptación, sección 19), así
que construir esas piezas a ciegas violaría el principio de este proyecto
de verificar todo contra el sistema real antes de darlo por bueno.

## Qué se hizo

### Contratos, indicadores y pipeline técnico

- `core/enums.py`: todos los enums de la sección 7.1, más `Trigger`
  (sección 21.1) y dos adiciones documentadas (`no_setup`,
  `sl_distance_invalid`).
- `core/schemas/technical.py`, `risk.py`, `decision.py`: `TechnicalSignal`,
  `RiskVerdict`, `DecisionRecord` tal como los define la sección 7.
- `services/technical/indicators.py`: EMA, ATR (Wilder), RSI, volumen
  relativo, rango rodante — cálculo propio en pandas/numpy.
  `candles_to_frame` descarta explícitamente cualquier vela cuyo
  `close_time` sea posterior a "ahora" (anti look-ahead).
- `services/scanner/regime.py`: régimen de BTC en 4h (EMA50/EMA200 +
  pendiente + percentil 90 histórico de ATR%), sección 8.3.
- `services/scanner/filters.py`: los 4 filtros duros de la sección 8.2,
  evaluados sin cortocircuitar.
- `services/technical/setups.py` + `signal_builder.py`: detección de
  ruptura de rango con confirmación de volumen (hipótesis de la sección
  3.1) y construcción de `TechnicalSignal` (SL = mínimo del rango −
  0.5×ATR14, TP con R:R bruto ≥ 2.0). `compute_breakout_frame` es la
  versión **vectorizada** que reutiliza también el backtest (sin
  duplicar lógica, regla crítica sección 6).

### Risk engine y journal

- `services/risk/engine.py`, `sizing.py`, `portfolio_state.py`: los 5
  checks por operación + 7 checks de cartera de la sección 9 (incluido
  el filtro de régimen como check más, y el killswitch), sizing por
  riesgo fijo fraccional (0.5% default), `PortfolioSnapshot` construido
  desde `trade_entries`/`trade_exits`/`equity_snapshots`/`system_state`
  (tablas de la migración `0002`).
- `journal/decision_logger.py`: persiste `DecisionRecord` en
  `decision_logs` siempre, acepte o rechace.

### Orquestación compartida — scanner manual y automático

- `services/scanner/scanner.py`: **pipeline único** (`evaluate_asset`,
  `evaluate_regime`, `decide_final_action`) que usan tanto `/analiza`
  como el ciclo automático — ver "regla crítica" sección 6, aplicada
  también a la orquestación, no solo a las fórmulas de la señal.
- `scripts/analiza.py`: equivalente CLI de `/analiza <PAR> [operar]`
  (sección 21.2), refactorizado para llamar a `services/scanner/scanner.py`
  en vez de duplicar el pipeline. Universo abierto (descarga on-demand,
  sección 21.3), límite `MANUAL_MAX_PER_HOUR`, rechazo de stablecoins
  (sección 21.5).
- **`app/scheduler.py`: el scanner automático (`trigger=scheduled`) ya
  está enganchado.** Cada ciclo (`SCAN_INTERVAL_MINUTES`): ingesta →
  lee esos mismos datos de DB (sin repetir llamadas a Binance) → corre
  el pipeline sobre los 10 activos de `UNIVERSE` → un `decision_log` por
  activo, siempre (principio 5: "todo se registra, incluidas las
  no-operaciones").
- En ambos modos ("operar" manual y automático): si el risk engine
  aprobaría la entrada pero el executor no existe todavía, se registra
  `final_action=watchlist` con aviso explícito — nunca se inventa una
  ejecución (fail-closed).

### Killswitch con rearme manual

- `scripts/halt.py` / `scripts/rearm.py`: CLI para halt/rearme manual
  (sección 9.2: "requiere rearme manual por CLI/endpoint", sección 15:
  "Endpoint/CLI de halt manual inmediato"). Persisten en `system_state`.
- `app/main.py` (`/health`) ahora reporta el `system_state` real leído de
  DB en vez de un valor fijo.
- `tests/integration/test_killswitch.py`: **test de integración contra
  Postgres real** que confirma que `halt` bloquea el check
  `system_not_halted` del risk engine (con todo lo demás en verde) y que
  `rearm` lo desbloquea — sin rearme, sigue bloqueado indefinidamente
  (nunca automático).

### Backtesting walk-forward (vectorbt)

- `backtests/download_history.py`: descarga paginada (Binance público,
  solo lectura) de histórico y lo persiste en `candles` — reutilizable
  para todo el sistema, no solo para backtesting.
- `backtests/strategy_breakout.py`: señales vectorizadas (`generate_signals`,
  `run_portfolio`) que reutilizan `compute_breakout_frame` y las
  constantes de `signal_builder.py` (`GROSS_RR_TARGET`,
  `STOP_ATR_BUFFER`) — nunca reimplementadas.
- `backtests/walk_forward.py`: walk-forward sin solape (in-sample 6
  meses, out-of-sample 2 meses), grid search de `RANGE_LOOKBACK_CANDLES`
  × `VOLUME_CONFIRM_MULT` en cada ventana in-sample, aplicado a la
  siguiente ventana out-of-sample. Reporta SOLO métricas out-of-sample
  concatenadas (sección 14). Cada retorno de trade se reescala de
  "retorno del instrumento" a "impacto real en el equity" usando el
  mismo sizing por riesgo fijo fraccional que el sistema en vivo — ver
  bug corregido más abajo.
- `backtests/RESULTS.md`: resultados reales sobre 800 días de histórico,
  10 pares × 2 timeframes, 60 folds walk-forward, 322 trades
  out-of-sample. **Expectancy neta positiva** (+0.327% de equity por
  trade), profit factor 2.45, drawdown máximo −8.87% (bajo el 10% del
  killswitch). Comparado contra buy&hold BTC (−7.68% mismo periodo) y
  contra 0%, con limitaciones documentadas explícitamente (histórico
  corto, curva de equity secuencial no multi-posición, slippage
  aproximado, etc.).

## Bug encontrado y corregido durante el backtesting

La primera versión del walk-forward reportaba un "retorno compuesto" de
**+408.198%** (sic) — resultado del retorno que da `vectorbt` por trade
(retorno del instrumento, asumiendo ~100% del capital invertido) sin
reescalarlo al sizing real del sistema (`RISK_PER_TRADE=0.5%` del
equity). Componer 322 trades así, secuencialmente, con retornos de
instrumento de hasta +34% cada uno, da un número absurdo. Corregido:
cada trade se reescala a `equity_impact = return_instrumento ×
(RISK_PER_TRADE / sl_pct_del_trade)` antes de calcular cualquier métrica
de cartera — con esto el retorno compuesto baja a +183.5%, un número
todavía optimista (por la simplificación de curva secuencial, ver
limitaciones en `RESULTS.md`) pero ya no fantasioso. Este es exactamente
el tipo de error que un test de regresión con fixture fijo (añadido,
`tests/unit/test_backtest_regression.py`) no habría atrapado — protege
la fórmula de la señal, no la interpretación de sus resultados; quedó
documentado aquí para que no se repita el razonamiento erróneo.

## Verificación end-to-end (real, no simulada) — cronología

**Sesión 1**: `docker compose up -d --build` con DB limpia → migraciones
`0001`+`0002` aplican → scheduler ingesta el universo real →
`/analiza SOLUSDT` (informe y operar), `/analiza LTCUSDT` (fuera de
universo, descarga on-demand, rechazado por liquidez), `/analiza
USDCUSDT` (rechazado como stablecoin). Filas confirmadas en
`decision_logs`/`regime_log` vía `psql`.

**Sesión 2** (regresión, DB limpia de nuevo): mismo resultado sin cambios
de código — `/analiza BTCUSDT` y `/analiza DOGEUSDT operar` confirman el
mismo comportamiento con activos distintos.

**Sesión 3** (esta): tras enganchar el scanner automático, arranque
limpio → el ciclo automático corrió sobre los 10 pares y **aprobó un
setup real sin intervención manual** (`XRPUSDT`, `range_breakout`,
`rr_net_of_fees≈1.91`, `final_action=watchlist` con aviso de "aprobado,
executor pendiente"); los otros 9 activos quedaron correctamente
rechazados con motivos variados (`no_setup`, `liquidity`, `spread`,
`sl_distance_invalid`). 10 `decision_logs` con `trigger=scheduled`
confirmados por `psql`. Descarga de 800 días de histórico (19200 velas
1h + 4800 velas 4h por par) y walk-forward completo ejecutados con
éxito. Test de integración del killswitch verificado contra Postgres
real.

**Sesión 4** (esta, paper ledger interno): arranque limpio → migración
`0003` aplica sin problemas → varios ciclos automáticos completos
(ingesta → scan → paper positions) sin errores. `/analiza <PAR> operar`
probado contra 9 de los 10 pares del universo (el décimo, `BNBUSDT`,
topó con `MANUAL_MAX_PER_HOUR` — confirma que el rate limit sigue
funcionando igual que antes); ningún setup real disponible en el momento
de la verificación, así que el camino de apertura de posición de papel no
se disparó por ese lado, pero sí quedó verificado exhaustivamente por
integración (ver abajo): apertura + cierre directo con aserciones exactas
sobre fees/pnl/equity, y `update_open_positions` cerrando una posición
real al detectar TP en velas insertadas a mano. `/estado` verificado
mostrando sistema, régimen, equity/drawdown, posiciones abiertas (0) y
resumen de cerradas (0) correctamente.

## Tests

**57 tests unitarios** en verde (sin DB, `docker compose run --rm
--no-deps app uv run pytest -v`):

- `test_indicators.py`, `test_regime.py`, `test_filters.py`,
  `test_setups.py`: propiedades conocidas y casos límite de cada pieza
  del pipeline técnico.
- `test_risk_engine.py`: caso base con todos los checks en verde + un
  caso por cada uno de los 14 checks fallando en aislamiento (sección
  18), verificando que ningún otro check se ve afectado.
- `test_anti_lookahead.py`: una vela en curso se descarta.
- `test_backtest_regression.py` **(nuevo)**: fixture fijo de velas →
  entrada, SL/TP, trade y retorno EXACTOS conocidos (sección 18:
  "el backtest sobre un fixture fijo... debe producir métricas exactas
  conocidas"). Protege contra divergencia silenciosa entre
  `services/technical/` y `backtests/`.

- `test_paper_ledger.py` **(nuevo)**: `evaluate_exit` — SL, TP, SL gana a
  TP en la misma vela (criterio conservador), invalidación técnica, sin
  salida todavía, anti look-ahead (vela de la propia señal ignorada), y
  salida por tiempo en sus dos variantes (`hours`/`days`).

**3 tests de integración** (necesita Postgres real, `docker compose exec
app uv run pytest tests/integration -v`):

- `test_killswitch.py`: halt manual bloquea el risk engine, rearme lo
  desbloquea, sin recuperación automática.
- `test_paper_ledger.py` **(nuevo)**: apertura + cierre de una posición de
  papel con aserciones exactas de fees/pnl/equity contra
  `services/risk/portfolio_state.py`; `update_open_positions` cerrando
  una posición real al detectar TP en velas insertadas a mano.

`mypy --strict` sobre `core/`, `services/risk/` y `services/execution/`
(nuevo en el scope): sin errores (15 archivos).

## Decisiones de diseño documentadas (añadidas al documento de spec)

Todas marcadas `# DECISION` en el código y reflejadas en
`ESPECIFICACION_SISTEMA_TRADING.md`:

1. `RejectionReason.no_setup` — el filtro de "movimiento" no tenía motivo
   de rechazo propio.
2. `RejectionReason.sl_distance_invalid` — los checks `sl_distance_min`/
   `max` no tenían motivo de rechazo propio; `notional_min` reutiliza
   `exchange_filter`.
3. `RANGE_LOOKBACK_CANDLES` (20) y `VOLUME_CONFIRM_MULT` (1.5) — nuevos
   parámetros de config para el detector de ruptura.
4. `PAPER_STARTING_EQUITY_USDT` (10000) — equity de arranque del risk
   engine mientras no exista ningún `equity_snapshots` real.
5. Tabla `system_state` — necesaria para persistir el killswitch/halt a
   través de reinicios.
6. Clasificación de `conviction` por umbral de `rel_volume` — regla
   conservadora explícita, calibrable con más datos (sección 13).
7. Paper ledger interno (sección 10.1) — simulación pura sobre velas
   reales en vez de fills de testnet, mientras no existan credenciales.

## Hallazgo relevante (confirmado, no resuelto — es una calibración, no un bug)

`sl_distance_max` (distancia entrada→SL ≤ 4×ATR14) es el check individual
que más rechaza setups reales en modo `/analiza` (SOL, ETH, BTC, en
distintas sesiones). El backtest walk-forward, sin embargo, SÍ produce
trades reales con expectancy positiva usando el mismo umbral — sugiere
que el filtro es correcto en agregado aunque descarte muchos casos
puntuales al mirar un solo activo a mano. No se ha tocado el umbral (es
un default explícito del documento, sección 9.1); es exactamente el tipo
de calibración que la sección 13 reserva para datos de fase 1-2.

## Qué quedó fuera (bloqueado por credenciales)

- `services/execution/binance_executor.py` real (OCO en testnet,
  idempotencia, fills parciales, rate limits) — sección 10. Necesita
  `BINANCE_API_KEY`/`SECRET` de testnet. El paper ledger interno
  (`services/execution/paper_ledger.py`) cubre la parte de "ver
  rentabilidad" sin necesitar esto — ver `# DECISION` en la sección 10.1.
- `services/monitor/` real contra órdenes de exchange (position_monitor,
  exit_rules) — sección 11. El paper ledger ya cubre TP/SL/invalidación/
  tiempo de forma simulada; lo que falta aquí es específico de vigilar
  órdenes reales (estado de OCO, reconciliación de fills).
- `notifications/telegram.py`. Necesita
  `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. `/estado` ya existe como
  comando CLI (`scripts/estado.py`), independiente de Telegram.
- Reconciliación (`services/execution/reconciler.py`). Depende del
  executor real y de las keys de testnet.

## Cómo reproducir la verificación

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec app uv run python -m scripts.analiza SOLUSDT
docker compose exec app uv run python -m scripts.analiza SOLUSDT operar
docker compose exec app uv run python -m scripts.estado
docker compose exec app uv run python -m scripts.halt "prueba"
docker compose exec app uv run python -m scripts.rearm
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
docker compose run --rm --no-deps app uv run pytest -v
docker compose exec app uv run pytest tests/integration -v
docker compose run --rm --no-deps app uv run mypy
docker compose down
```
