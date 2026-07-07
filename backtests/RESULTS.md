# Resultados de backtesting — walk-forward (Fase 1)

Generado: 2026-07-07 (recálculo tras los fixes de bugs #10-#18,
`docs/CODE_REVIEW_2026-07-07.md`). Base de datos reiniciada el
2026-07-07 (ver `CLAUDE.md`) y re-poblada con
`backtests/download_history.py --days 800` (240.000 velas, 10 activos ×
2 timeframes, 2024-04-28 → 2026-07-07) antes de este cálculo. Código:
`backtests/strategy_breakout.py` + `backtests/walk_forward.py`, misma
lógica de señales que `services/technical/setups.py` +
`signal_builder.py`, y misma lógica de ENTRADAS/SALIDAS que
`services/execution/paper_ledger.py` (`evaluate_pending_fill` +
`evaluate_exit`, regla crítica sección 6 — sin divergencia backtest/live).

## Qué cambió respecto a la versión anterior (2026-07-06)

La versión anterior de este documento (expectancy +0.096%/trade, 738
trades, drawdown −7.03%) ya modelaba invalidación técnica y salida por
tiempo, pero seguía asumiendo un **fill inmediato y optimista** al
`entry_ref` de la señal (bug #11, `CLAUDE.md`) — un precio que en vivo
el mercado no siempre llega a confirmar.

El fix (`simulate_trades` ahora llama a `paper_ledger.evaluate_pending_fill`
antes de `evaluate_exit`, mismo código que usa el paper ledger, no una
segunda implementación) simula una **orden pendiente** con `entry_zone`
y `entry_ttl_minutes`: si el precio no toca la zona de entrada dentro del
TTL, el trade se descarta (no cuenta como operación). Esto reduce el
número de trades que sí llegan a abrirse y, sobre todo, cambia qué
trades sobreviven — algunos que antes "entraban" en el peor precio de la
señal ahora ni siquiera se abren, y otros abren en un punto distinto de
la zona. Consecuencia esperada (así lo anticipaba `CLAUDE.md` antes de
este recálculo) y confirmada: la expectancy neta **baja de forma
significativa** respecto al valor anterior, aunque sigue siendo
positiva (ver resultados).

## Configuración exacta

```json
{
  "lookback_grid": [10, 20, 30],
  "volume_mult_grid": [1.2, 1.5, 2.0],
  "in_sample_months": 6,
  "out_sample_months": 2,
  "fees": "0.001",
  "slippage": 0.0002,
  "risk_per_trade": 0.005,
  "universe": ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT"],
  "timeframes": ["1h", "4h"]
}
```

- Histórico: 800 días (2024-04-28 → 2026-07-07), descargado con
  `backtests/download_history.py` desde la API pública de Binance
  (producción, solo lectura) y persistido en `candles` (240.000 velas —
  redescargado en esta ronda porque la BD se había reiniciado el
  2026-07-07 y solo conservaba la ingesta en vivo desde entonces).
- Walk-forward **sin solape**: se optimiza `RANGE_LOOKBACK_CANDLES` ×
  `VOLUME_CONFIRM_MULT` (grid 3×3) en cada ventana in-sample de 6 meses
  por `expectancy` (en términos de equity, ver más abajo), se aplica esa
  combinación a los 2 meses out-of-sample siguientes, y se rueda. **Solo
  se reportan métricas de las ventanas out-of-sample, concatenadas.**
  3 folds completos por combinación activo×timeframe (20 combinaciones →
  60 folds).
- `STOP_ATR_BUFFER` (0.5×ATR14) y `GROSS_RR_TARGET` (R:R bruto 2.0) **no
  se optimizan**: son la fórmula fija de construcción de la señal
  (sección 7.2), importada literalmente desde `signal_builder.py`.
- El horizonte máximo (48h para 1h, 7 días para 4h — sección 10.1), la
  invalidación técnica y el modelo de fill pendiente (`entry_zone` +
  `entry_ttl_minutes`) tampoco se optimizan: son la misma constante y el
  mismo código que usa `paper_ledger` en vivo.
- Si el horizonte de una señal cae después de la última vela disponible
  en la ventana, el trade queda sin resolver (censura por límite de
  datos) y se **descarta** — no se cuenta como cerrado.

## Metodología de sizing (importante para interpretar los números)

`simulate_trades` devuelve, por trade, el retorno del **instrumento**
(variación de precio entrada→salida, neto de fees 0.1%/lado y slippage
pesimista 2bps — sección 14). El sistema real nunca arriesga el 100% del
equity en un trade: dimensiona por **riesgo fijo fraccional** (sección
9.3), `size_quote = equity × RISK_PER_TRADE / distancia_relativa_al_SL`.
Cada retorno de instrumento se reescala a `equity_impact = return_instrumento
× (RISK_PER_TRADE / sl_pct_del_trade)` antes de calcular expectancy,
drawdown o cualquier métrica de cartera.

**Simplificación documentada**: la curva de equity concatena los trades
out-of-sample de los 10 pares × 2 timeframes **ordenados cronológicamente
y compuestos de forma secuencial**, como si solo pudiera existir una
posición abierta a la vez. El sistema real permite hasta `MAX_POSITIONS=3`
simultáneas (sección 9.2), así que esto **serializa** trades que en
producción podrían solaparse — subestima el uso de capital, nunca lo
sobreestima. Válido como cota conservadora, no como simulación exacta de
cartera multi-posición.

## Resultados out-of-sample (concatenados, N=719 trades)

| Métrica | Valor anterior (2026-07-06, fill optimista) | **Valor actual (fill pendiente, corregido)** |
|---|---|---|
| Trades OOS | 738 | **719** |
| Win rate | 28.0% | **27.4%** |
| Avg win (equity) | +0.69% | **+0.555%** |
| Avg loss (equity) | −0.14% | **−0.149%** |
| **Expectancy por trade (equity, neto de fees+slippage)** | +0.096% | **+0.0438%** |
| Profit factor | 1.99 | **1.41** |
| Sharpe simple (informativo, ruidoso) | 0.23 | **0.12** |
| Max drawdown (curva secuencial) | −7.03% | **−13.78%** ⚠️ |
| Retorno compuesto (curva secuencial, N trades) | +102.3% | **+36.4%** |

### Baseline obligatoria (sección 3.2)

| | Retorno mismo periodo (2024-04-28 → 2026-07-07) |
|---|---|
| **Estrategia** (compuesto secuencial, ver limitación arriba) | **+36.4%** |
| Buy & hold BTC | **+0.10%** |
| No operar | 0% |

El buy & hold de BTC sale prácticamente plano en esta ventana exacta
(precio de cierre casi idéntico al inicio de la ventana de 800 días,
pese a la volatilidad intermedia) — no es un error de cálculo, es el
resultado real de tomar el primer y último cierre de esa ventana
concreta; no debe leerse como "BTC no se movió", sino como que el punto
de entrada y salida de la ventana coinciden en precio.

### Desglose por activo/timeframe (folds out-of-sample)

Igual que en la versión anterior (mismo universo, mismos 3 folds por
combinación); ver el JSON completo de esta corrida
(`docker compose exec app uv run python -m backtests.walk_forward`) para
el detalle fold a fold si hace falta auditar un activo concreto.

## Interpretación (sección 14: expectancy ≤ 0 → no pasar a fase 2)

La expectancy out-of-sample neta **sigue siendo positiva**
(+0.0438% de equity por trade) con el modelo de fill corregido, con
profit factor 1.41. La hipótesis de la sección 3.1 **no queda
falsada** — pero el margen se ha reducido a menos de la mitad del que
sugería el cálculo anterior, y dos señales piden prudencia antes de
alegrarse:

1. **El profit factor cayó de 1.99 a 1.41.** Sigue por encima de 1, pero
   el colchón frente a errores de modelado (slippage real, comisiones
   variables, fills peores de los simulados) es mucho más estrecho.
2. **⚠️ El max drawdown (−13.78%) SUPERA el `drawdown_killswitch` del
   sistema (10%, `app/config.py:41`).** Esto es un hallazgo nuevo y
   relevante: en la curva secuencial simulada, hubo un tramo donde el
   sistema en vivo se habría **detenido solo** antes de llegar a este
   mínimo. El backtest actual **no modela el kill-switch** — simplemente
   dejó correr todos los trades sin parar — así que el −13.78% es "lo
   que habría pasado si nadie hubiera frenado el sistema", no una
   predicción de lo que el sistema real habría hecho. En la práctica esto
   corta en las dos direcciones: el kill-switch real habría evitado parte
   de esa caída (mejor que −13.78%), pero también habría dejado fuera de
   mercado al sistema durante la recuperación posterior, con lo que la
   expectancy y el retorno compuesto reales tras un evento así son
   inciertos — ninguna cifra de este documento cubre ese escenario.

**Conclusión honesta**: el sistema sigue teniendo sentido para continuar
acumulando paper trading real (sección 15) y cumplir los gates
(≥60 días / ≥30 trades desde el código corregido y congelado el
2026-07-07), pero con expectativas de edge notablemente más modestas que
las de la versión anterior de este documento, y con una señal de alerta
concreta sobre el tamaño de drawdown que puede tolerar la cartera. Antes
de considerar dinero real, sería razonable: (a) simular explícitamente el
efecto del kill-switch sobre esta misma curva de trades (parar la
simulación cuando drawdown ≥ 10% y medir cuánto tiempo/trades se pierde
hasta la reactivación), y (b) vigilar de cerca el drawdown real acumulado
en paper trading, no solo la expectancy media.

## Simulación del kill-switch de drawdown (10%) — 2026-07-07

Pregunta abierta en la sección anterior: el max drawdown sin restricción
(−13.78%) supera el `drawdown_killswitch` (10%, `app/config.py`), que en
producción bloquea NUEVAS entradas mientras el equity esté ≥10% por
debajo de su pico histórico (`checks["drawdown_killswitch"]`,
`services/risk/engine.py`) — el backtest anterior no aplicaba ese freno.
`backtests/simulate_killswitch.py` reutiliza la misma lista de trades
OOS y aplica esa regla exacta sobre la curva secuencial.

**Limitación explícita de esta simulación**: es el escenario más
pesimista posible. El freno real es un gate que se reevalúa en vivo y se
autodesbloquea en cuanto el equity recupera el 90% del pico — pero en
esta curva de una sola posición a la vez, el equity SOLO se mueve con
trades que se ejecutan; si no se permiten entradas nuevas, no hay forma
de recuperar, así que una vez disparado el freno **no se levanta nunca**
en este modelo. El sistema real permite hasta 3 posiciones concurrentes:
otras posiciones ya abiertas en otros activos antes del freno podrían
seguir cerrando y recuperar equity sin necesidad de nuevas entradas —
algo que esta simulación de una sola posición no puede representar. Por
tanto, esto es una cota **peor** que lo que probablemente habría pasado
en producción, no una predicción exacta.

| Métrica | Sin freno (referencia) | **Con freno (peor caso)** |
|---|---|---|
| Trades aplicados | 719 | **535** (184 nunca se habrían abierto) |
| Primer disparo del freno | — | **2026-03-13** |
| Win rate | 27.4% | **30.5%** |
| Expectancy por trade (equity) | +0.044% | **+0.066%** |
| Profit factor | 1.41 | **1.63** |
| Sharpe simple | 0.12 | **0.18** |
| Max drawdown | −13.78% | **−10.11%** |
| Retorno compuesto | +36.4% | **+41.9%** |

### Interpretación

Incluso en el escenario más pesimista posible (freno permanente, cero
recuperación, 184 trades de los últimos ~4 meses del histórico jamás
ejecutados), **todas las métricas mejoran**: más expectancy, más profit
factor, más retorno compuesto, y el drawdown se queda justo en el
umbral (−10.11%, ligeramente por encima del 10% porque el chequeo ocurre
ANTES de abrir un trade, no impide que ESE trade concreto termine
empujando el equity un poco más abajo — mismo comportamiento que tendría
el risk engine real).

Esto es una señal positiva concreta: en esta racha histórica, los 184
trades que el freno habría evitado eran, en conjunto, perjudiciales para
la curva — el kill-switch hizo exactamente su trabajo. **Importante no
sobre-generalizar**: esto es UNA secuencia histórica concreta, no una
prueba de que el freno siempre mejora el resultado medio — su función es
acotar el peor caso (evitar que una mala racha se profundice), no
maximizar la media; que aquí también haya mejorado la media es una
buena señal adicional, no la garantía de su propósito.

## Limitaciones honestas de este backtest (léelas antes de confiar en los números)

1. **Solo ~800 días de historia → solo 3 folds por combinación.** Un
   walk-forward robusto querría muchos más folds (varios años más de
   historia) para que la expectancy no dependa de un régimen de mercado
   concreto.
2. **Curva de equity secuencial, no cartera multi-posición real** (ver
   metodología arriba). El +36.4% compuesto es una cota que ignora
   solapamiento de posiciones entre activos; no es una promesa de
   retorno real.
3. **El kill-switch de drawdown (10%) no se modela en la simulación**
   (nuevo, ver sección "Interpretación" arriba) — el max drawdown de
   −13.78% asume que nadie detiene el sistema, algo que en vivo sí
   ocurriría. Cuantificar el efecto real queda pendiente.
4. **Slippage aproximado (2 bps uniformes)**, no el tick size real por
   símbolo — sección 14 pide "1 tick + 2bps"; el tick size exacto por
   símbolo y momento no se modela.
5. **Backtest por timeframe independiente**: en vivo, `signal_builder`
   prioriza 4h y cae a 1h; aquí cada timeframe se testea aislado, sin
   modelar esa preferencia ni evitar doble conteo si ambos timeframes
   generan señal en fechas cercanas para el mismo activo.
6. **Grid de calibración limitado** (3×3 combinaciones de
   `RANGE_LOOKBACK_CANDLES`/`VOLUME_CONFIRM_MULT`); no se ha explorado
   un grid más fino ni otros multiplicadores de ATR.
7. **Universo pequeño y correlacionado** (10 majors, casi todos beta-BTC):
   los 719 trades no son 719 apuestas independientes; el drawdown real en
   un evento de correlación total (crash de BTC) podría ser peor que lo
   que sugiere este backtest.
8. **Filtro de régimen BTC y filtros duros del scanner NO se aplican
   aquí**: el régimen es factible con solo velas pero requiere alinear
   velas 4h de BTC con las de cada activo (trabajo aparte); los filtros
   duros (liquidez/spread/frescura) necesitarían histórico de
   `market_snapshots`, que hoy no se persiste para backtest. Su ausencia
   probablemente **sobreestima** el número de trades reales (algunas de
   estas señales se habrían rechazado en vivo).

## Cómo reproducir

```bash
docker compose up -d --build
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
docker compose exec app uv run python -m backtests.simulate_killswitch
```
