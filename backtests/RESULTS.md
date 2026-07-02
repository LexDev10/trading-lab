# Resultados de backtesting — walk-forward (Fase 1)

Generado: 2026-07-02. Commit base: `dadd5d8` (más los cambios de esta
sesión, commiteados junto con este archivo). Código: `backtests/strategy_breakout.py`
+ `backtests/walk_forward.py`, misma lógica de señales que
`services/technical/setups.py` + `signal_builder.py` (regla crítica,
sección 6 — sin divergencia backtest/live).

## Configuración exacta

```json
{
  "lookback_grid": [10, 20, 30],
  "volume_mult_grid": [1.2, 1.5, 2.0],
  "in_sample_months": 6,
  "out_sample_months": 2,
  "fees": 0.001,
  "slippage": 0.0002,
  "risk_per_trade": 0.005,
  "universe": ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT"],
  "timeframes": ["1h", "4h"]
}
```

- Histórico: 800 días (2024-04-23 → 2026-07-02), descargado con
  `backtests/download_history.py` desde la API pública de Binance
  (producción, solo lectura) y persistido en `candles`.
- Walk-forward **sin solape**: se optimiza `RANGE_LOOKBACK_CANDLES` ×
  `VOLUME_CONFIRM_MULT` (grid 3×3) en cada ventana in-sample de 6 meses
  por `expectancy` (en términos de equity, ver más abajo), se aplica esa
  combinación a los 2 meses out-of-sample siguientes, y se rueda. **Solo
  se reportan métricas de las ventanas out-of-sample, concatenadas.**
  3 folds completos por combinación activo×timeframe (20 combinaciones →
  60 folds), limitado por los 800 días de histórico disponibles.
- `STOP_ATR_BUFFER` (0.5×ATR14) y `GROSS_RR_TARGET` (R:R bruto 2.0) **no
  se optimizan**: son la fórmula fija de construcción de la señal
  (sección 7.2), importada literalmente desde `signal_builder.py` — si se
  optimizaran aquí sin cambiar el sistema en vivo, backtest y live
  divergirían (prohibido por la regla crítica de la sección 6).

## Metodología de sizing (importante para interpretar los números)

`vectorbt.Portfolio.from_signals` devuelve, por trade, el retorno del
**instrumento** (variación de precio entrada→salida, neto de fees y
slippage). El sistema real nunca arriesga el 100% del equity en un
trade: dimensiona por **riesgo fijo fraccional** (sección 9.3),
`size_quote = equity × RISK_PER_TRADE / distancia_relativa_al_SL`. Cada
retorno de instrumento se reescala a `equity_impact = return_instrumento
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

## Resultados out-of-sample (concatenados, N=322 trades)

| Métrica | Valor |
|---|---|
| Trades OOS | 322 |
| Win rate | 55.6% |
| Avg win (equity) | +1.00% |
| Avg loss (equity) | −0.51% |
| **Expectancy por trade (equity, neto de fees)** | **+0.327%** |
| Profit factor | 2.45 |
| Sharpe simple (informativo, ruidoso) | 0.42 |
| Max drawdown (curva secuencial) | −8.87% |
| Retorno compuesto (curva secuencial, 322 trades) | +183.5% |

### Baseline obligatoria (sección 3.2)

| | Retorno mismo periodo (2024-04-23 → 2026-07-02) |
|---|---|
| **Estrategia** (compuesto secuencial, ver limitación arriba) | **+183.5%** |
| Buy & hold BTC | **−7.68%** |
| No operar | 0% |

### Desglose por activo/timeframe (folds out-of-sample)

| Activo | 1h trades OOS | 4h trades OOS |
|---|---|---|
| BTCUSDT | 18 | 9 |
| ETHUSDT | 22 | 9 |
| SOLUSDT | 21 | 7 |
| BNBUSDT | 21 | 7 |
| XRPUSDT | 25 | 9 |
| ADAUSDT | 21 | 11 |
| DOGEUSDT | 31 | 7 |
| AVAXUSDT | 25 | 8 |
| LINKUSDT | 25 | 10 |
| DOTUSDT | 25 | 11 |

3 folds walk-forward por combinación (limitado por los 800 días de
histórico disponibles; más historia daría más folds y una validación más
robusta — ver limitaciones).

## Interpretación (sección 14: expectancy ≤ 0 → no pasar a fase 2)

La expectancy out-of-sample neta es **positiva** (+0.327% de equity por
trade), con profit factor 2.45 y drawdown máximo (−8.87%) por debajo del
`DRAWDOWN_KILLSWITCH` del 10%. Bajo esta evidencia, **la hipótesis de la
sección 3.1 no queda falsada** y el proyecto puede avanzar hacia
`ENVIRONMENT=testnet` en vivo (paper trading, sección 15) para acumular
los ≥30 trades / ≥60 días que exigen los gates de capital real — el
backtest por sí solo **no** es uno de los gates de la sección 15, solo
habilita seguir a la siguiente etapa de validación.

## Limitaciones honestas de este backtest (léelas antes de confiar en los números)

1. **Solo 800 días de historia → solo 3 folds por combinación.** Un
   walk-forward robusto querría muchos más folds (varios años más de
   historia) para que la expectancy no dependa de un régimen de mercado
   concreto. Estos 800 días incluyen mercado bajista de BTC (−7.68%);
   ampliar el histórico cuando haya más datos disponibles es la mejora
   más importante pendiente.
2. **Curva de equity secuencial, no cartera multi-posición real** (ver
   metodología arriba). El +183.5% compuesto es una cota que ignora
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
   los 322 trades no son 322 apuestas independientes; el drawdown real en
   un evento de correlación total (crash de BTC) podría ser peor que lo
   que sugiere este backtest.

## Cómo reproducir

```bash
docker compose up -d --build
docker compose exec app uv run python -m backtests.download_history --days 800
docker compose exec app uv run python -m backtests.walk_forward
```
