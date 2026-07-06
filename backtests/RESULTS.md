# Resultados de backtesting — walk-forward (Fase 1)

Generado: 2026-07-06. Commit base: `a253c89` (más el fix del bug #4 de
`CHANGELOG.md`, commiteado junto con este archivo). Código:
`backtests/strategy_breakout.py` + `backtests/walk_forward.py`, misma
lógica de señales que `services/technical/setups.py` +
`signal_builder.py`, y misma lógica de SALIDAS que
`services/execution/paper_ledger.py::evaluate_exit` (regla crítica,
sección 6 — sin divergencia backtest/live, ver más abajo).

## Qué cambió respecto a la versión anterior (2026-07-02)

La versión anterior de este documento (expectancy +0.327%, 322 trades)
estaba calculada con un backtest que **solo modelaba SL/TP**
(`vectorbt.Portfolio.from_signals(sl_stop=..., tp_stop=...)`), ciego a
las otras dos salidas que sí aplica el paper ledger en vivo:
invalidación técnica (cierre de vela < `range_high` de la señal) y
salida por tiempo (horizonte máximo). Como la entrada queda por encima
de `invalidation_level`, esa era en la práctica la salida más frecuente
y mucho más cercana que el SL — el backtest anterior **no representaba
la operativa real** (bug #4 de la revisión de código, `CLAUDE.md`).

El fix (`simulate_trades` en `backtests/strategy_breakout.py`) elimina
`vectorbt` de la ruta de decisión: para cada señal, llama directamente a
`paper_ledger.evaluate_exit` — el mismo código que usa el paper ledger,
no una segunda implementación — para decidir cuándo y a qué precio sale
cada trade. El fee/PnL también se calcula con la misma fórmula
(`compute_trade_pnl`, extraída de `paper_ledger.close_position`).
Consecuencia esperada y observada: muchos más trades (cierran antes),
win rate bajo (la invalidación es casi siempre una salida perdedora,
pero mucho más barata que dejar correr hasta el SL) y una expectancy
neta bastante menor que la del backtest anterior — pero **sigue siendo
positiva** (ver resultados).

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

- Histórico: 803 días (2024-04-23 → 2026-07-06), descargado con
  `backtests/download_history.py` desde la API pública de Binance
  (producción, solo lectura) y persistido en `candles` (241k velas, ya
  en DB — no hizo falta volver a descargar para este fix).
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
- El horizonte máximo (48h para 1h, 7 días para 4h — sección 10.1) y la
  invalidación técnica tampoco se optimizan: son la misma constante y el
  mismo código que usa `paper_ledger.evaluate_exit` en vivo.
- Si el horizonte de una señal cae después de la última vela disponible
  en la ventana, el trade queda sin resolver (censura por límite de
  datos) y se **descarta** — no se cuenta como cerrado. Esto además
  resuelve la limitación conocida #9 (trades abiertos al final de la
  ventana contaminando la expectancy).

## Metodología de sizing (importante para interpretar los números)

`simulate_trades` devuelve, por trade, el retorno del **instrumento**
(variación de precio entrada→salida, neto de fees 0.1%/lado y slippage
pesimista 2bps — sección 14). El sistema real nunca arriesga el 100% del
equity en un trade: dimensiona por **riesgo fijo fraccional** (sección
9.3), `size_quote = equity × RISK_PER_TRADE / distancia_relativa_al_SL`.
Cada retorno de instrumento se reescala a `equity_impact = return_instrumento
× (RISK_PER_TRADE / sl_pct_del_trade)` antes de calcular expectancy,
drawdown o cualquier métrica de cartera. Sin este reescalado, un solo
trade con SL ancho e instrumento volátil podría "mover" el 15-20% del
equity, algo que el risk engine real nunca permitiría.

**Simplificación documentada**: la curva de equity concatena los trades
out-of-sample de los 10 pares × 2 timeframes **ordenados cronológicamente
y compuestos de forma secuencial**, como si solo pudiera existir una
posición abierta a la vez. El sistema real permite hasta `MAX_POSITIONS=3`
simultáneas (sección 9.2), así que esto **serializa** trades que en
producción podrían solaparse — subestima el uso de capital, nunca lo
sobreestima. Válido como cota conservadora, no como simulación exacta de
cartera multi-posición (eso requeriría un backtester de cartera completo,
fuera de alcance de este backtest de la capa técnica).

## Resultados out-of-sample (concatenados, N=738 trades)

| Métrica | Valor |
|---|---|
| Trades OOS | 738 |
| Win rate | 28.0% |
| Avg win (equity) | +0.69% |
| Avg loss (equity) | −0.14% |
| **Expectancy por trade (equity, neto de fees+slippage)** | **+0.096%** |
| Profit factor | 1.99 |
| Sharpe simple (informativo, ruidoso) | 0.23 |
| Max drawdown (curva secuencial) | −7.03% |
| Retorno compuesto (curva secuencial, 738 trades) | +102.3% |

El win rate bajo (28%) frente al del backtest anterior (55.6%) es
esperado: la invalidación técnica corta la mayoría de los trades antes
de que puedan alcanzar el TP (R:R bruto 2:1), pero también los corta
mucho antes de llegar al SL — de ahí que el avg loss (−0.14%) sea muy
inferior al avg win (+0.69%), y que la expectancy siga siendo positiva
pese al bajo win rate.

### Baseline obligatoria (sección 3.2)

| | Retorno mismo periodo (2024-04-23 → 2026-07-06) |
|---|---|
| **Estrategia** (compuesto secuencial, ver limitación arriba) | **+102.3%** |
| Buy & hold BTC | **−5.79%** |
| No operar | 0% |

### Desglose por activo/timeframe (folds out-of-sample)

| Activo | 1h trades OOS | 4h trades OOS |
|---|---|---|
| BTCUSDT | 62 | 22 |
| ETHUSDT | 52 | 21 |
| SOLUSDT | 46 | 11 |
| BNBUSDT | 70 | 19 |
| XRPUSDT | 67 | 18 |
| ADAUSDT | 56 | 21 |
| DOGEUSDT | 54 | 16 |
| AVAXUSDT | 54 | 18 |
| LINKUSDT | 59 | 21 |
| DOTUSDT | 39 | 12 |

3 folds walk-forward por combinación (limitado por los ~803 días de
histórico disponibles; más historia daría más folds y una validación más
robusta — ver limitaciones).

## Interpretación (sección 14: expectancy ≤ 0 → no pasar a fase 2)

La expectancy out-of-sample neta sigue siendo **positiva** (+0.096% de
equity por trade) incluso modelando invalidación y salida por tiempo con
el mismo código que el paper ledger, con profit factor 1.99 y drawdown
máximo (−7.03%) por debajo del `DRAWDOWN_KILLSWITCH` del 10%. La
hipótesis de la sección 3.1 **no queda falsada**, aunque el margen es
bastante más estrecho que el que sugería el backtest anterior (con el
bug de salidas sin corregir) — el sistema sigue teniendo sentido para
seguir acumulando paper trading real (sección 15), pero con expectativas
de edge más modestas.

## Limitaciones honestas de este backtest (léelas antes de confiar en los números)

1. **Solo ~803 días de historia → solo 3 folds por combinación.** Un
   walk-forward robusto querría muchos más folds (varios años más de
   historia) para que la expectancy no dependa de un régimen de mercado
   concreto. Ampliar el histórico cuando haya más datos disponibles es
   la mejora más importante pendiente.
2. **Curva de equity secuencial, no cartera multi-posición real** (ver
   metodología arriba). El +102.3% compuesto es una cota que ignora
   solapamiento de posiciones entre activos; no es una promesa de
   retorno real.
3. **Slippage aproximado (2 bps uniformes)**, no el tick size real por
   símbolo — sección 14 pide "1 tick + 2bps"; el tick size exacto por
   símbolo y momento no se modela.
4. **Backtest por timeframe independiente**: en vivo, `signal_builder`
   prioriza 4h y cae a 1h; aquí cada timeframe se testea aislado, sin
   modelar esa preferencia ni evitar doble conteo si ambos timeframes
   generan señal en fechas cercanas para el mismo activo.
5. **Grid de calibración limitado** (3×3 combinaciones de
   `RANGE_LOOKBACK_CANDLES`/`VOLUME_CONFIRM_MULT`); no se ha explorado
   un grid más fino ni otros multiplicadores de ATR (esos están fijados
   por diseño, ver sección "Configuración exacta").
6. **Universo pequeño y correlacionado** (10 majors, casi todos beta-BTC):
   los 738 trades no son 738 apuestas independientes; el drawdown real en
   un evento de correlación total (crash de BTC) podría ser peor que lo
   que sugiere este backtest.
7. **Filtro de régimen BTC y filtros duros del scanner NO se aplican
   aquí** (residual del bug #4, fuera de alcance de este fix): el
   régimen es factible con solo velas pero requiere alinear velas 4h de
   BTC con las de cada activo (trabajo aparte); los filtros duros
   (liquidez/spread/frescura) necesitarían histórico de
   `market_snapshots`, que hoy no se persiste para backtest. Su ausencia
   probablemente **sobreestima** el número de trades reales (algunas de
   estas señales se habrían rechazado en vivo).

## Cómo reproducir

```bash
docker compose up -d --build
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
```
