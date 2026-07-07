# Revisión de código 2026-07-07 — bugs, mejoras y plan de corrección

Revisión completa del pipeline (scanner → señal → risk engine → paper
ledger → backtest → capa fundamental → scheduler), posterior a la del
2026-07-06 (bugs 1-4, ver `CHANGELOG.md`). Numeración continua con la de
`CLAUDE.md`: los bugs de este documento empiezan en el **#10**.

**Regla general de este documento**: los bugs #10, #11 y #12 corrompen la
estadística del paper trading que deben medir los gates de la sección 15.
Igual que ocurrió con los bugs 1-2, **cualquier histórico de paper
acumulado antes de corregirlos deberá purgarse** y el contador de ≥60
días / ≥30 trades reiniciarse desde código corregido y congelado.
Corregirlos ANTES de empezar a acumular histórico "bueno".

Prioridad recomendada: #10 → #11 → #12 → #16 → #13 → resto.

---

## BUGS CRÍTICOS

### #10 — Una misma ruptura abre posiciones duplicadas (sin dedupe por vela ni por posición abierta)

**Dónde**: `services/risk/engine.py`, `services/scanner/scanner.py`,
`services/execution/paper_ledger.py::open_position`.

**Síntoma**: el ciclo corre cada `SCAN_INTERVAL_MINUTES` (15 min), pero
las velas 1h/4h solo cambian cada 1h/4h. La misma vela cerrada con
`breakout=True` se re-detecta en hasta 4 ciclos consecutivos (señal 1h) o
16 (señal 4h). Nada lo impide:

- El risk engine no tiene ningún check "ya existe posición abierta en
  este activo".
- Los cooldowns (`portfolio_state._asset_cooldown_active`,
  `_losses_cooldown_active`) solo miran `trade_exits` — no aplican
  mientras la posición sigue abierta.
- `run_scan_cycle` no dedupe por `candle_close_time` de la señal.

Un solo breakout puede abrir 2-3 posiciones idénticas en 30-45 min hasta
chocar con `max_positions` o las caps de exposición (con SL ancho el
sizing es pequeño y las caps no frenan). Además **diverge del backtest**:
`simulate_trades` serializa una posición a la vez por activo/timeframe,
así que el paper que valida los gates no reproduce lo backtesteado.

**Corrección (dos capas, ambas):**

1. **Check de cartera nuevo en el risk engine** (fail-closed, queda en
   `checks` y se persiste como el resto):
   - `PortfolioSnapshot` gana el campo `asset_has_open_position: bool`.
   - `portfolio_state.build_portfolio_snapshot` lo calcula con una query
     `EXISTS` sobre `trade_entries` filtrando `environment == ENVIRONMENT`,
     `status == 'open'` y `asset == asset` (¡filtrar environment, regla
     #3 del CLAUDE.md!).
   - En `evaluate_risk`: `checks["no_open_position_same_asset"]` y, si
     falla, `RejectionReason` nuevo `position_already_open` (añadir al
     enum `core/enums.py::RejectionReason`; si se prefiere no tocar el
     enum, reutilizar `max_positions` con `# DECISION:` — el detalle
     exacto siempre queda en `checks`).

2. **Dedupe por vela de señal** (evita que la MISMA vela reintente tras
   un cierre rápido dentro de su misma hora):
   - Persistir `signal_candle_close_time` en `trade_entries` (migración
     Alembic nueva, nullable para filas históricas).
   - Antes de `open_position`, el scanner comprueba que no exista ya un
     `trade_entry` (cualquier status, mismo environment) con el mismo
     `(asset, timeframe, signal_candle_close_time)`. Alternativa sin
     migración: query sobre `decision_logs` con
     `final_action='enter'` y el mismo `technical_jsonb->>'candle_close_time'`
     — menos robusta (JSONB), preferible la columna.

**Tests de aceptación:**
- Unit (risk engine): con `asset_has_open_position=True` el verdict es
  `approved=False` y `checks["no_open_position_same_asset"] is False`.
- Integración (scanner): dos llamadas a `run_scan_cycle` con `now` +15
  min sobre las mismas velas → exactamente 1 `trade_entry`.
- Integración: tras cerrar la posición, la MISMA vela de señal no reabre;
  una vela de señal NUEVA sí (respetando cooldown_asset).

---

### #11 — Modelo de fill optimista: fill inmediato a un precio que el mercado nunca confirmó (`ENTRY_TTL_MINUTES` muerto)

**Dónde**: `services/execution/paper_ledger.py::open_position`,
`services/scanner/scanner.py` (entry_ref), `backtests/strategy_breakout.py`.

**Síntoma**: la posición de papel se rellena inmediatamente a
`entry_ref = (range_high + close) / 2`, que por construcción está POR
DEBAJO del último close (se asume un pullback que quizá nunca ocurre).
En una estrategia de breakout esto sesga sistemáticamente a favor: los
mejores breakouts no retroceden — en real esos trades no se habrían
llenado o se habrían llenado peor. Con una expectancy OOS de
+0.096%/trade, este sesgo puede ser mayor que todo el edge.
`entry_ttl_minutes` (45) existe en `app/config.py` pero **no se usa en
ningún sitio**: el spec preveía una orden límite con TTL y se perdió.

**Corrección (paper ledger y backtest, MISMO código — regla sección 6):**

1. Nuevo estado `TradeStatus.pending` (enum + migración si el valor no
   cabe en la columna actual; `status` es String(20), cabe).
2. `open_position` pasa a crear la entrada con `status='pending'`,
   guardando `entry_zone_low`, `entry_zone_high` (columnas nuevas,
   migración Alembic) y SIN insertar exposición: `_get_open_positions`
   sigue filtrando `status == 'open'`, y las pendientes NO cuentan para
   exposición pero SÍ para el check #10 (una pending en el activo bloquea
   otra señal).
3. Función nueva y única `evaluate_pending_fill(entry, candles, settings,
   now) -> FillDecision | None` en `paper_ledger`:
   - Solo velas cerradas, `open_time > entry_time`, `close_time <= now`
     (mismo criterio anti look-ahead que `evaluate_exit`).
   - Si `candle.low <= entry_zone_high` → fill. Precio de fill
     conservador: `min(entry_zone_high, candle.open)` si la vela abre ya
     dentro/debajo de la zona, si no `entry_zone_high` (nunca el punto
     medio: es el precio límite más pesimista de la zona).
   - Si `now - entry_time >= entry_ttl_minutes` sin fill →
     `status='expired'` (nuevo valor), se loguea y notifica.
   - DECISIÓN a documentar: si la MISMA vela que llena también toca el SL,
     criterio conservador = fill + SL en esa vela (pérdida completa).
4. `update_open_positions` procesa primero pendientes (fill/expire) y
   luego abiertas (exits) — y `evaluate_exit` de una posición llenada
   empieza en la vela del fill, no en la de la señal.
5. **Backtest**: `simulate_trades` usa `evaluate_pending_fill` importada
   (no reimplementar): tras la señal en la vela `i`, busca el fill en las
   velas siguientes dentro del TTL; si no hay fill, la señal se descarta
   (equivale a `expired`). Recalcular `backtests/RESULTS.md` y actualizar
   el walk-forward — **la expectancy va a bajar**; si pasa a ser negativa,
   eso es información real, no un bug del fix.

**Tests de aceptación:**
- Unit: señal cuya siguiente vela nunca baja a la zona → sin fill; tras
  TTL → `expired`, sin `trade_exit`, sin snapshot de equity.
- Unit: vela que abre por debajo de la zona → fill a `candle.open`.
- Regresión backtest: mismo dataset de `test_backtest_regression.py`,
  verificar que solo entran los trades con pullback dentro del TTL.

---

### #12 — Orden invertido en el ciclo: se abre antes de procesar los cierres pendientes

**Dónde**: `app/scheduler.py::market_cycle_job`.

**Síntoma**: el orden actual es ingesta → `_scan_cycle` (ABRE) →
`_update_paper_positions` (CIERRA). El scan evalúa `daily_loss_limit`,
`drawdown_killswitch`, `max_positions` y el cooldown de 2 SL con el
estado ANTERIOR a los cierres que ya están en las velas recién
ingeridas. Ejemplo: dos posiciones tocaron SL en la última hora → el
cooldown de 2 SL y la pérdida diaria deberían bloquear nuevas entradas,
pero el scan corre antes de registrarlas y abre igualmente.

**Corrección**: invertir el orden en `market_cycle_job`:
ingesta → `_update_paper_positions` → `_scan_cycle`. Mantener el
fail-closed escalonado (si los cierres fallan, NO escanear: mejor no
abrir con estado de cartera desconocido — documentar con `# DECISION:`).

**Tests de aceptación:**
- Integración: cartera con 2 posiciones cuyo SL está tocado en velas ya
  persistidas + una señal válida en otro activo → el ciclo completo NO
  abre (cooldown 2 SL activo tras procesar los cierres primero).

---

## BUGS MENORES

### #13 — Items envenenados bloquean el clasificador para siempre

**Dónde**: `services/fundamental/classify.py::_pending_news/_pending_social`.

**Síntoma**: un item que falla (red, JSON inválido, enum fuera de
esquema) no deja rastro en `item_classifications`, así que vuelve a
seleccionarse el ciclo siguiente (orden `fetched_at asc`, `limit` =
`fundamental_classify_batch_size` = 10). Diez items que fallen de forma
determinista consumen TODO el presupuesto de cada corrida y ningún item
nuevo se clasifica jamás (head-of-line blocking).

**Corrección** (append-only, sin UPDATE — regla PIT sección 12.1): cuando
`_classify_and_persist` falla, insertar igualmente una fila en
`item_classifications` con `stance='unknown'`, `veto=False`,
`event_types=[]`, `summary='classification_failed'` y el error en
`output_jsonb` (p.ej. `{"error": "...", "failed": true}`). El item deja
de estar "pendiente" y una reclasificación futura (otro modelo/prompt)
sigue siendo posible como fila nueva. Excluir las filas `failed` de
`asset_has_active_veto`/`get_latest_stance` no hace falta: stance
`unknown` y `veto=False` ya son neutros.

**Test**: item que provoca `ValidationError` → siguiente corrida procesa
items NUEVOS (el envenenado ya no aparece en `_pending_*`).

### #14 — Scorecard: horizonte truncado, duplicados y multi-tag

**Dónde**: `services/fundamental/scorecard.py`.

**Síntomas**:
1. `_price_at_or_before(classified_at + 72h)` devuelve la última vela
   disponible aunque falten 60h de datos: un item del domingo mide su
   "retorno a 72h" sobre 12h reales.
2. Re-ejecutar el job de la misma semana duplica filas (sin unique en
   `(week, stance, horizon)`).
3. Solo se puntúa `asset_tags[0]`.

**Corrección**:
1. En `compute_weekly_scorecard`, descartar el punto si
   `candles[-1].open_time < item.classified_at + horizon_delta` (no hay
   dato suficiente para ese horizonte todavía).
2. Migración: unique constraint sobre `(week, stance, horizon)` +
   upsert `ON CONFLICT DO UPDATE` (el scorecard NO es almacén PIT, es una
   tabla derivada recalculable — documentar con `# DECISION:`).
3. Iterar todos los `asset_tags`, no solo el primero (un item puede
   afectar a BTC y ETH a la vez).

### #15 — Alertas de Telegram antes del commit (notificaciones fantasma)

**Dónde**: `app/scheduler.py::_scan_cycle` (commit único al final),
`services/execution/paper_ledger.py::open_position/close_position`
(envían Telegram dentro de la transacción).

**Síntoma**: si el scan falla a mitad de universo (p.ej. `ValueError` de
`get_min_notional` en el activo n.º 7), las posiciones "abiertas" de los
activos 1-6 se revierten con el rollback... pero sus alertas de Telegram
ya salieron. El operador cree que hay posiciones que no existen.

**Corrección** (dos cambios complementarios):
1. `run_scan_cycle`: envolver la evaluación de CADA activo en
   `try/except` — un activo que falla se loguea y registra
   `final_action=reject` con `# DECISION:`, sin tumbar el resto del
   universo (fail-closed por activo, igual que la ingesta fundamental es
   fail-closed por fuente).
2. Sacar los `send_message` de `open_position`/`close_position`:
   devolver el texto (o acumular en una lista) y que el CALLER (scheduler)
   los envíe DESPUÉS de `session.commit()`. La alerta pasa a significar
   "persistido", no "intentado".

### #16 — El veto fundamental pisa un SL ya ocurrido en velas anteriores

**Dónde**: `services/execution/paper_ledger.py::evaluate_exit` (rama
`veto_active`).

**Síntoma**: con veto activo se sale al último close aunque una vela
anterior de `relevant` ya hubiera tocado el SL — el exit registrado
(tipo y precio) es el del veto, no el del SL, que pudo ser peor (o
mejor: TP). Distorsiona tanto el PnL como la atribución por `exit_type`.

**Corrección**: en la rama de veto, recorrer primero `relevant` con la
misma lógica SL/TP/invalidación; si alguna vela dispara, devolver ESE
exit (ocurrió antes en el tiempo). Solo si ninguna vela cerrada disparó
nada, cerrar por veto al último close. Orden: mover el bloque
`if veto_active` DESPUÉS del bucle `for candle in relevant`, antes de la
salida por tiempo.

**Test**: veto activo + vela intermedia con `low <= sl` →
`exit_type='closed_sl'` a precio SL, no `closed_fundamental_veto`.

### #17 — `daily_loss_limit` cuenta por `exit_time` de vela, no por tiempo de proceso

**Dónde**: `services/risk/portfolio_state.py::_get_daily_realized_pnl_pct`.

**Síntoma**: mismo patrón que el bug #1 corregido en equity. Un cierre
procesado hoy a las 00:15 con `exit_time` (tiempo de vela) de ayer 23:40
no cuenta en la pérdida diaria de hoy — y la de ayer ya no se vuelve a
evaluar. Pérdidas reales escapan del límite diario.

**Corrección**: filtrar por el tiempo de PROCESO. Opción limpia: join
con `position_events` (`event_type='paper_exit'`) no sirve — su `ts` es
el de la vela. La fuente correcta ya existe: los `EquitySnapshot` se
insertan con `ts` de proceso; pero no llevan el pnl por trade. Solución
mínima: columna nueva `processed_at TIMESTAMPTZ` en `trade_exits`
(migración; backfill con `exit_time` para filas históricas) y filtrar
`TradeExit.processed_at >= day_start`. `close_position` la rellena con
`now`. Mantener `exit_time` intacto (cuándo ocurrió la salida en
mercado); solo la agregación diaria usa proceso — mismo criterio
equity-curve del fix #1.

**Test**: cierre con `exit_time` de ayer procesado hoy → cuenta en la
pérdida diaria de hoy.

---

## RIESGO DE DISEÑO (decisión de producto, no un fix mecánico)

### #18 — Un LLM clasificando contenido no autenticado puede cerrar posiciones a mercado

**Dónde**: `services/fundamental/classify.py` + `paper_ledger.evaluate_exit`
(rama veto) + `services/fundamental/veto.py`.

El spec dice "ningún LLM en el camino señal→orden" y formalmente la
salida es categórica, pero `veto=true` sobre un post de Reddit fuerza el
cierre inmediato de una posición. Es contenido no autenticado con prompt
injection trivial (un post redactado para que el modelo devuelva
`veto: true` sobre BTC) con capacidad de ejecutar salidas.

**Mitigación recomendada** (elegir y documentar en el spec):
1. El veto originado en fuentes `social` solo BLOQUEA entradas nuevas
   (check del risk engine); el cierre forzoso de posiciones queda
   reservado a fuentes `news` — y con corroboración: ≥2 items
   independientes (sources distintas) con `veto=true` sobre el mismo
   activo dentro de la ventana.
2. Medir la ventana de veto desde `published_at` (con fallback a
   `classified_at` si es NULL y cap de antigüedad), no desde
   `classified_at`: hoy un backlog viejo clasificado tarde genera vetos
   "frescos" de noticias de hace días. Requiere propagar
   `published_at`/`source` a `item_classifications` (columnas nuevas,
   append-only, migración) o join contra `news_items`/`social_items` por
   `(item_kind, item_id)`.

---

## MEJORAS

### M1 — Mark-to-market de posiciones abiertas
La equity solo se mueve con PnL realizado: el `drawdown_killswitch` es
ciego a pérdidas latentes de hasta 3 posiciones × varios días.
**Desarrollo**: al final de cada `market_cycle_job`, insertar un
`EquitySnapshot` con `equity = última equity realizada + Σ pnl no
realizado` (último close de vela cerrada por posición abierta, misma
fórmula `compute_trade_pnl` con fee solo de entrada... o más simple y
conservador: sin fees). Marcar el snapshot (columna `kind:
'realized'|'mark'` o convención en `open_positions`) para que
`get_latest_equity` de los CIERRES siga leyendo solo snapshots
realized — si no, el PnL realizado se contaminaría con marks. DECISION a
documentar: el killswitch SÍ lee el mark (es su propósito).

### M2 — Slippage coherente paper ↔ backtest
El backtest aplica 2 bps (`DEFAULT_SLIPPAGE`), el paper ledger ninguno.
**Desarrollo**: mover `DEFAULT_SLIPPAGE` a `Settings.slippage_bps`
(config + `.env.example`) y aplicarlo en `paper_ledger` (fill de entrada
hacia arriba, exit hacia abajo) igual que en backtest. Los gates deben
medirse con al menos el mismo pesimismo que el backtest.

### M3 — rr_net y sizing contra la invalidación, no contra el SL
Residual documentado que conviene subir de prioridad: como
`invalidation_level = range_high >= entry_ref`, casi ningún trade llega
al SL — la salida dominante es la invalidación. El riesgo real por trade
es MENOR que el dimensionado y el R:R declarado (1.8 neto contra SL) no
es el efectivo.
**Desarrollo**: en `evaluate_risk`, calcular un segundo par
`rr_net_effective` / `expected_loss_at_invalidation` usando
`invalidation_level` como pérdida esperada típica (el SL sigue siendo el
peor caso para sizing — no cambiar `calc_size_quote` sin recalibrar
`risk_per_trade`). Como mínimo, registrar ambos en el verdict para poder
comparar con datos de paper antes de cambiar el filtro de 1.8.

### M4 — Cap de exposición en `_equity_impact` del walk-forward
`Return * (risk_per_trade / sl_pct_at_entry)` puede implicar posiciones
>100% del equity que en vivo bloquearían `max_exposure_btc_beta`.
**Desarrollo**: `size_frac = min(risk_per_trade / sl_pct,
float(settings.max_exposure_btc_beta))` y escalar con `size_frac`.
Documentar en RESULTS.md que el backtest capa igual que el risk engine.

### M5 — Constantes fuera de config (viola la regla del spec)
Mover a `Settings` + `.env.example`: multiplicadores 1.0/4.0 ATR del SL
(`engine.py`), `GROSS_RR_TARGET` y `STOP_ATR_BUFFER`
(`signal_builder.py` — ojo: el backtest los importa, mantener una sola
fuente), el ×1.2 de `min_notional` (`engine.py`) y el ×2 de conviction
strong (`signal_builder.py`).

### M6 — Cachear `exchangeInfo` (min_notional)
Se pide por red en el camino de decisión cada vez que hay setup. Cambia
rarísimamente. **Desarrollo**: cache en memoria con TTL de 24h en
`BinanceMarketData.get_min_notional` (dict `{asset: (valor, fetched_at)}`).
Fail-closed: si expira y la red falla, NO operar ese activo (no usar el
valor caducado silenciosamente; documentar con `# DECISION:`).

### M7 — Scorecard también para el flag `veto`
El scorecard mide stances, pero el veto — la salida con más poder (cierra
posiciones) — no se evalúa contra retornos realizados. Añadir bucket
`veto=true`: ¿el precio realmente cayó a 4h/24h/72h tras un veto? Es el
dato que justificará (o no) mantener el cierre forzoso de #18.

---

## Orden de ejecución propuesto y verificación

| Paso | Ítems | Motivo |
|------|-------|--------|
| 1 | #12 (orden del ciclo) | Trivial y sin migraciones |
| 2 | #10 (dedupe/posición única) | Bloquea la validez de los gates |
| 3 | #11 (fill pending+TTL) + M2 | El cambio más grande; recalcular RESULTS.md |
| 4 | #16, #17, #15 | Correcciones acotadas del ledger/ciclo |
| 5 | #13, #14 | Capa fundamental |
| 6 | #18 + M7 | Decisión de producto + medición |
| 7 | M1, M3-M6 | Mejoras incrementales |

Tras CADA paso que toque `paper_ledger`/`portfolio_state`/`backtests`
(regla del CLAUDE.md):

```
docker compose run --rm --no-deps app uv run pytest -v
docker compose exec app uv run pytest tests/integration -v
docker compose exec app uv run mypy .
```

Y al cerrar los pasos 1-3: **purgar el histórico de paper** (equity,
trades) y reiniciar el contador de los gates — el histórico previo está
generado con los bugs #10-#12 activos. Actualizar `CHANGELOG.md`,
`README.md` (Limitaciones) y `CLAUDE.md` (lista de bugs corregidos /
pendientes) en el mismo commit que cada fix.
