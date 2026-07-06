# CLAUDE.md — contexto obligatorio antes de tocar código

Lee primero `ESPECIFICACION_SISTEMA_TRADING.md` (documento de trabajo
completo, fases y criterios de aceptación) y el `CHANGELOG.md`. Este
archivo resume los **bugs conocidos, corregidos y pendientes** de la
revisión de código del 2026-07-06, para que no se reintroduzcan ni se
olviden.

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
  (equity y salidas potencialmente corruptas). Los ≥60 días / ≥30 trades
  de los gates deben contarse desde código corregido y congelado.
- `scripts/check_live_gates.py` no existe todavía (sección 15 lo exige).
- Executor OCO real + reconciliación contra testnet: sin construir
  (bloqueado por credenciales). Los criterios de aceptación de Fase 1
  NO están completos hasta entonces.
- **Verificado el 2026-07-06 en Docker** (bugs 1-4): `docker compose run
  --rm --no-deps app uv run pytest -v` (72 unit) + `docker compose exec
  app uv run pytest tests/integration -v` (6 integration) + `mypy .`
  (165→169 errores frente al HEAD original, sin categorías nuevas — solo
  la misma deuda preexistente de anotaciones en tests). Repetir esta
  verificación tras cualquier cambio en
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
