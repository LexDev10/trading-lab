# CLAUDE.md — contexto obligatorio antes de tocar código

Lee primero `ESPECIFICACION_SISTEMA_TRADING.md` (documento de trabajo
completo, fases y criterios de aceptación) y el `CHANGELOG.md`. Este
archivo resume los **bugs conocidos, corregidos y pendientes** de las
revisiones de código del 2026-07-06 y 2026-07-07, para que no se
reintroduzcan ni se olviden.

---

## Bugs YA CORREGIDOS el 2026-07-07 — NO reintroducir

Detalle completo en `docs/CODE_REVIEW_2026-07-07.md` y `CHANGELOG.md`
(entrada 2026-07-07). Migraciones `0007`-`0009`. Reglas que esos fixes
dejaron establecidas:

10. **Nunca dos posiciones sobre el mismo activo/vela.**
    `PortfolioSnapshot.asset_has_open_position` (calculado en
    `portfolio_state.py`, filtra `status IN ('pending','open')`) es un
    check más del risk engine (`checks["no_open_position_same_asset"]`,
    `RejectionReason.position_already_open`) — nunca se salta. Segunda
    capa: `trade_entries.signal_candle_close_time` +
    `paper_ledger.signal_already_traded` — dedupe explícito por
    `(asset, timeframe, signal_candle_close_time)` antes de
    `open_position`. Cualquier camino nuevo hacia `open_position` debe
    pasar por ambos.

11. **`open_position` registra una orden PENDIENTE, nunca un fill inmediato.**
    `status='pending'` + `entry_zone_low`/`entry_zone_high` (columnas
    nuevas). El fill real lo decide `paper_ledger.evaluate_pending_fill`
    vela a vela contra la `entry_zone`, respetando
    `settings.entry_ttl_minutes` (existía en `Settings` desde el origen
    del proyecto pero nunca se usaba — el fill era inmediato y optimista
    a `entry_ref`, un precio que el mercado nunca confirmaba). Sin fill
    dentro del TTL → `status='expired'` (sin trade_exit, sin impacto en
    equity). **DECISION crítica**: `evaluate_pending_fill` SÍ admite la
    vela en curso (`open_time <= now`, sin exigir `close_time <= now`) —
    a diferencia de `evaluate_exit`. Es obligatorio: `entry_ttl_minutes`
    (45 por defecto) es menor que cualquier timeframe operado (1h/4h);
    exigir vela cerrada haría que la orden expirase siempre y ninguna
    posición se abriría jamás. `backtests/strategy_breakout.py::simulate_trades`
    reutiliza la misma función (regla sección 6) — **`backtests/RESULTS.md`
    quedó desactualizado por este fix y debe recalcularse** antes de
    usarse para decidir nada (la expectancy va a bajar).

12. **Orden del ciclo: cerrar SIEMPRE antes de escanear.**
    `app/scheduler.py::market_cycle_job` procesa
    `update_open_positions` ANTES de `run_scan_cycle` — al revés, el
    risk engine evaluaba `daily_loss_limit`/cooldown de 2 SL con cierres
    del propio ciclo todavía sin registrar.

13. **El clasificador nunca deja un item "pendiente para siempre".**
    `classify.py::_classify_and_persist` persiste una fila neutra
    (`stance=unknown`, `veto=False`, `summary=classification_failed`)
    también cuando el item FALLA — antes, un puñado de items que
    fallaran de forma determinista agotaba todo el presupuesto del batch
    en cada corrida (head-of-line blocking) y ningún item nuevo se
    clasificaba jamás.

14. **Scorecard: upsert, no INSERT; horizonte real, no truncado; todos los `asset_tags`.**
    Constraint único `(week, stance, horizon)` + `ON CONFLICT DO UPDATE`
    — recalcular la misma semana ya no duplica filas. Un punto se
    descarta si no hay vela real a la distancia del horizonte (antes se
    aproximaba con la última vela disponible). Itera TODOS los
    `asset_tags` de un item.

15. **Telegram se envía DESPUÉS del commit, nunca dentro de la transacción.**
    `open_position`/`close_position`/`update_open_positions`/
    `run_scan_cycle` devuelven el texto del mensaje en vez de llamar a
    `send_message` — el caller (`app/scheduler.py`, `scripts/analiza.py`)
    lo envía tras `session.commit()`. `run_scan_cycle` además evalúa cada
    activo dentro de un `try/except`: un fallo en un activo no debe
    tumbar el resto del universo.

16. **El veto fundamental nunca pisa un SL/TP/invalidación ya ocurrido.**
    En `evaluate_exit`, la rama `veto_active` se comprueba DESPUÉS del
    bucle de velas (SL/TP/invalidación), nunca antes — sigue siendo más
    urgente que la salida por tiempo, pero no reescribe una salida que
    el mercado ya había decidido en una vela anterior.

17. **`daily_loss_limit`/resumen diario agregan por tiempo de PROCESO.**
    Columna nueva `trade_exits.processed_at` (monotónica, rellenada por
    `close_position` con `now`) — mismo criterio que el fix del bug #1 en
    la curva de equity. `portfolio_state._get_daily_realized_pnl_pct` y
    `daily_summary._trades_today` filtran por `processed_at`, nunca por
    `exit_time` (tiempo de vela, puede quedar horas en el pasado).

18. **El cierre forzoso de una posición exige corroboración de fuentes NEWS.**
    `services/fundamental/veto.py::asset_has_active_closing_veto`
    (usada por `paper_ledger` para el cierre anticipado) exige
    `item_kind='news'` y al menos `settings.fundamental_veto_min_sources`
    (2 por defecto) valores DISTINTOS de `source` en veto dentro de la
    ventana. `asset_has_active_veto` (bloqueo de ENTRADAS nuevas, sin
    cambios de comportamiento) sigue aceptando cualquier fuente,
    incluida `social`. La ventana de decaimiento (`FUNDAMENTAL_VETO_HOURS`)
    se mide desde `published_at` (columna nueva en `item_classifications`,
    con fallback a `classified_at` si es NULL), no desde `classified_at`
    — un backlog viejo clasificado tarde ya no genera vetos "frescos".
    Motivo: un LLM clasificando contenido de Reddit no autenticado
    (prompt injection trivial) no debe poder forzar el cierre de una
    posición real por sí solo.

---

## Bugs YA CORREGIDOS el 2026-07-06 — NO reintroducir

Detalle completo en `CHANGELOG.md` (entrada 2026-07-06). Reglas que esos
fixes dejaron establecidas:

1. **Curva de equity: `ts` de proceso + lectura por `id`.**
   `EquitySnapshot` se inserta SIEMPRE con el tiempo de proceso (`now`),
   nunca con el `exit_time` de la vela; y "última equity" se lee por
   `id desc` (orden de inserción), nunca por `ts desc`
   (`portfolio_state.get_latest_equity`, `_get_drawdown_pct`,
   `scripts/estado.py`). Motivo: cierres procesados fuera de orden
   cronológico perdían PnL de la curva (afectaba a drawdown_killswitch y
   daily_loss_limit). Cualquier lector/escritor nuevo de
   `equity_snapshots` debe seguir el mismo criterio.

2. **Nunca evaluar la vela en curso en salidas.**
   `paper_ledger.evaluate_exit` filtra `close_time <= now` (mismo
   criterio anti look-ahead que `candles_to_frame`, sección 18 del spec).
   La ingesta SÍ persiste la kline en formación de Binance — cualquier
   consumidor nuevo de `candles` debe filtrarla explícitamente. La
   invalidación técnica es "por CIERRE" (vela cerrada), jamás sobre un
   close intermedio. `exit_time` jamás puede quedar en el futuro.

3. **Todas las queries de cartera filtran por `environment`.**
   La constante única es `services/risk/portfolio_state.py::ENVIRONMENT`
   (= "paper" hoy) — `paper_ledger`, `daily_summary` y `estado` la
   importan de ahí; no declarar copias locales. Cualquier query nueva
   sobre `trade_entries`/`trade_exits`/`equity_snapshots` DEBE filtrar
   environment. Cuando exista el executor real (testnet/live), esta
   constante pasará a derivar de `settings.environment`.

4. **Divergencia backtest ↔ paper en las SALIDAS.**
   `backtests/strategy_breakout.py::simulate_trades` ya NO simula SL/TP
   con `vectorbt` — llama directamente a `paper_ledger.evaluate_exit`
   (mismo código, no una reimplementación) para decidir invalidación
   técnica y salida por tiempo además de SL/TP. `evaluate_exit` tipa su
   parámetro `entry` como el `Protocol PaperEntryLike` (no `TradeEntry`)
   para poder aceptar también el `SimulatedEntry` del backtest;
   `max_hold_for_horizon` (antes `_max_hold`) y `compute_trade_pnl` (fees/
   PnL, extraída de `close_position`) son ahora públicas y las reutiliza
   el backtest — no declarar una segunda fórmula de fees/exit en ningún
   sitio nuevo. `backtests/RESULTS.md` está recalculado con el motor
   corregido (expectancy positiva pero bastante más modesta que antes del
   fix: +0.096%/trade, 738 trades OOS, win rate 28%). **Residual sin
   cubrir** (no bloquea, documentado en README/RESULTS.md): el backtest
   sigue sin aplicar el filtro de régimen BTC ni los filtros duros del
   scanner (necesitarían histórico de `market_snapshots`, no persistido
   hoy para backtest), y `rr_net` del risk engine se sigue calculando
   contra el SL, no contra la invalidación.

---

## Bugs/inconsistencias CONOCIDOS y AÚN SIN CORREGIR

Menores (aceptados por ahora, documentados en README "Limitaciones"):

5. La vela en la que ocurre la entrada queda excluida para siempre del
   seguimiento de SL/TP (`open_time > entry_time`): hasta 4h sin vigilar
   tras abrir.
6. Salidas por gap: si una vela abre por debajo del SL, el exit se
   registra al precio exacto del SL (optimista).
7. Walk-forward: las ventanas IS/OOS comparten 1 vela de frontera
   (`df.loc` inclusivo en ambos extremos).
8. La frescura de datos del scanner solo se comprueba sobre velas 1h; una
   señal 4h puede nacer de velas 4h obsoletas.

(El antiguo bug #9 —trades aún abiertos al final de la ventana entrando
en la expectancy del walk-forward— quedó resuelto como efecto colateral
del fix del bug #4: `simulate_trades` descarta cualquier trade cuyo
horizonte caiga después de la última vela disponible.)

---

## Deuda operativa antes de dinero real (además de los gates, sección 15)

- **Purgar/reiniciar el histórico de paper trading**: los datos
  acumulados ANTES del 2026-07-06 se generaron con los bugs 1-2 activos
  (equity y salidas potencialmente corruptas), y los acumulados ANTES
  del 2026-07-07 con los bugs 10-12 activos (posiciones duplicadas,
  fill optimista, orden del ciclo). Los ≥60 días / ≥30 trades de los
  gates deben contarse desde código corregido y congelado — la base de
  datos se reinició por completo el 2026-07-07 para partir de cero con
  el esquema y el código corregidos.
- **`backtests/RESULTS.md` — RECALCULADO el 2026-07-07** con el modelo de
  fill pendiente corregido (bug #11): expectancy bajó de +0.096% a
  +0.0438%/trade (719 trades OOS), profit factor 1.99→1.41, sigue
  positiva. Max drawdown de la curva secuencial sin restricciones:
  −13.78%, por encima de `drawdown_killswitch` (10%, `app/config.py`).
  **Cuantificado con `backtests/simulate_killswitch.py`** (nuevo script,
  mismo día): aplicando el freno del 10% sobre esa misma lista de
  trades, incluso en el escenario más pesimista posible (freno
  permanente sin recuperación — limitación explícita del modelo de una
  sola posición secuencial), TODAS las métricas mejoran: expectancy
  +0.066%, profit factor 1.63, drawdown se queda en −10.11%, retorno
  compuesto +41.9% (vs +36.4% sin freno). Ver sección "Simulación del
  kill-switch" de `backtests/RESULTS.md` — el kill-switch parece cumplir
  su función en esta historia concreta, sin perjudicar el resultado
  medio (no generalizar como garantía para toda secuencia futura).
- `scripts/check_live_gates.py` no existe todavía (sección 15 lo exige).
- Executor OCO real + reconciliación contra testnet: sin construir
  (bloqueado por credenciales). Los criterios de aceptación de Fase 1
  NO están completos hasta entonces.
- **Verificado el 2026-07-06 en Docker** (bugs 1-4): `docker compose run
  --rm --no-deps app uv run pytest -v` (72 unit) + `docker compose exec
  app uv run pytest tests/integration -v` (6 integration) + `mypy .`
  (165→169 errores frente al HEAD original, sin categorías nuevas — solo
  la misma deuda preexistente de anotaciones en tests).
- **Verificado el 2026-07-07 en Docker** (bugs 10-18): `docker compose
  exec app uv run pytest -q` (128 unit) + `docker compose exec app uv run
  pytest tests/integration -q` (25 integration), todos en verde; `mypy .`
  sin categorías de error nuevas (mismo criterio que el 2026-07-06).
  **Importante — sin bind mount** (ver memoria de sesión): el contenedor
  `app` NO ve cambios del host en caliente; hace falta
  `docker compose up -d --build app` tras CADA edición antes de volver a
  correr tests, o se estará testeando código obsoleto (o, peor, un
  `alembic upgrade head` fallará con "Can't locate revision" si el
  contenedor viejo corre contra migraciones que ya no existen en el
  código montado). Repetir esta verificación tras cualquier cambio en
  `paper_ledger`/`portfolio_state`/`backtests`.
- `.env` contiene un token real de Telegram (no está en git, verificado);
  no moverlo a código ni a ejemplos, y rotarlo si la carpeta se comparte.

---

## Reglas de trabajo (recordatorio del spec, secciones 2, 6, 20)

- Fail-closed siempre; ante ambigüedad, opción conservadora + comentario
  `# DECISION:` (y actualizar el spec si añade campos/parámetros).
- Lógica de señales compartida entre `services/technical/` y
  `backtests/` — mismo código importado, nunca dos implementaciones.
- `Decimal` para todo precio/cantidad; los floats solo en cálculo interno
  de indicadores (pandas).
- Todos los parámetros a `app/config.py` + `.env.example`; nada
  hardcodeado.
- Ningún LLM en el camino señal→orden; salidas LLM solo categóricas.
- Cada fase terminada → actualizar `README.md`, `CHANGELOG.md` y
  `docs/PHASE_N_REPORT.md`.
