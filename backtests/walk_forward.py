"""Walk-forward obligatorio (sección 14): optimiza `RANGE_LOOKBACK_CANDLES`
y `VOLUME_CONFIRM_MULT` en una ventana in-sample de 6 meses, valida en una
ventana out-of-sample de 2 meses, rueda sin solape. Reporta SOLO métricas
out-of-sample concatenadas — nunca resultados in-sample como si fueran de
rendimiento real (eso sería sobreajuste disfrazado).

Uso (con el stack levantado, tras `backtests.download_history`):
    docker compose exec app uv run python -m backtests.walk_forward
"""

import asyncio
import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from app.config import Settings, get_settings
from backtests.strategy_breakout import DEFAULT_SLIPPAGE, simulate_trades
from core.git_info import get_git_sha
from core.schemas.market import Timeframe
from db.session import get_session
from services.data.persistence import get_all_candles

LOOKBACK_GRID = [10, 20, 30]
VOLUME_MULT_GRID = [1.2, 1.5, 2.0]
IN_SAMPLE_MONTHS = 6
OUT_SAMPLE_MONTHS = 2
MIN_CANDLES_FOR_FOLD = 60  # margen sobre el lookback/volumen máximos del grid

TIMEFRAMES: tuple[Timeframe, ...] = ("1h", "4h")


@dataclass
class FoldResult:
    asset: str
    timeframe: str
    is_start: str
    is_end: str
    oos_end: str
    chosen_lookback: int
    chosen_volume_mult: float
    is_trades: int
    oos_trades: int


@dataclass
class TradeRecord:
    asset: str
    timeframe: str
    entry_time: str
    return_pct: float          # retorno del propio instrumento (precio entrada->salida)
    equity_impact_pct: float   # fracción del equity TOTAL, con sizing real (RISK_PER_TRADE)


@dataclass
class WalkForwardResult:
    folds: list[FoldResult] = field(default_factory=list)
    oos_trades: list[TradeRecord] = field(default_factory=list)


def _candles_to_indexed_frame(candles) -> pd.DataFrame:
    rows = [
        {
            "open_time": c.open_time,
            "close_time": c.close_time,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
            "quote_volume": float(c.quote_volume),
        }
        for c in candles
    ]
    df = pd.DataFrame(rows).sort_values("open_time")
    return df.set_index("open_time")


def _expectancy(returns: pd.Series) -> float:
    if len(returns) == 0:
        return 0.0
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    win_rate = len(wins) / len(returns)
    loss_rate = len(losses) / len(returns)
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = abs(losses.mean()) if len(losses) else 0.0
    return win_rate * avg_win - loss_rate * avg_loss


def _run_trades(
    df_window: pd.DataFrame,
    lookback: int,
    volume_mult: float,
    settings: Settings,
    asset: str,
    timeframe: Timeframe,
) -> pd.DataFrame:
    """Trades simulados con salidas realistas (SL/TP/invalidación/tiempo,
    ver `simulate_trades` — bug #4 del CHANGELOG). Ya trae
    `sl_pct_at_entry`, necesaria para traducir el retorno del INSTRUMENTO
    al retorno del EQUITY TOTAL con sizing real (riesgo fijo fraccional,
    sección 9.3) — ver `_equity_impact`."""
    if len(df_window) < MIN_CANDLES_FOR_FOLD:
        return pd.DataFrame()
    return simulate_trades(df_window, lookback, volume_mult, settings, asset, timeframe, slippage=DEFAULT_SLIPPAGE)


def _equity_impact(trades: pd.DataFrame, risk_per_trade: float) -> pd.Series:
    """Fracción de equity TOTAL ganada/perdida por trade, asumiendo que
    CADA trade se dimensiona para arriesgar exactamente `risk_per_trade`
    del equity si toca el stop (sección 9.3: `size_quote = equity ×
    RISK_PER_TRADE / distancia_relativa_al_SL`). El `Return` de
    `simulate_trades` es el retorno del INSTRUMENTO (asume invertir ~todo
    el capital); aquí se re-escala a lo que de verdad movería la cuenta
    con el sizing real del sistema."""
    if len(trades) == 0:
        return pd.Series(dtype=float)
    return trades["Return"] * (risk_per_trade / trades["sl_pct_at_entry"])


def walk_forward_asset(
    df: pd.DataFrame, asset: str, timeframe: Timeframe, risk_per_trade: float, settings: Settings
) -> WalkForwardResult:
    result = WalkForwardResult()
    if len(df) == 0:
        return result

    cursor = df.index[0]
    end = df.index[-1]

    while True:
        is_start = cursor
        is_end = is_start + pd.DateOffset(months=IN_SAMPLE_MONTHS)
        oos_end = is_end + pd.DateOffset(months=OUT_SAMPLE_MONTHS)
        if oos_end > end:
            break

        is_df = df.loc[is_start:is_end]
        oos_df = df.loc[is_end:oos_end]

        best: tuple[float, int, float, int] | None = None  # (expectancy, lookback, vol_mult, n_trades)
        for lookback, vol_mult in itertools.product(LOOKBACK_GRID, VOLUME_MULT_GRID):
            trades = _run_trades(is_df, lookback, vol_mult, settings, asset, timeframe)
            if len(trades) == 0:
                continue
            # Selección de parámetros in-sample por expectancy en términos
            # de EQUITY real (no del instrumento), consistente con la
            # métrica primaria de la sección 3.3.
            exp = _expectancy(_equity_impact(trades, risk_per_trade))
            if best is None or exp > best[0]:
                best = (exp, lookback, vol_mult, len(trades))

        if best is None:
            # Ningún parámetro produjo trades in-sample: no hay base para
            # elegir, se documenta el fold como vacío y se avanza.
            cursor = oos_end
            continue

        _, chosen_lookback, chosen_vol_mult, is_trade_count = best
        oos_trades = _run_trades(oos_df, chosen_lookback, chosen_vol_mult, settings, asset, timeframe)

        result.folds.append(
            FoldResult(
                asset=asset,
                timeframe=timeframe,
                is_start=str(is_start),
                is_end=str(is_end),
                oos_end=str(oos_end),
                chosen_lookback=chosen_lookback,
                chosen_volume_mult=chosen_vol_mult,
                is_trades=is_trade_count,
                oos_trades=len(oos_trades),
            )
        )
        if len(oos_trades) > 0:
            oos_equity_impact = _equity_impact(oos_trades, risk_per_trade)
            for (_, row), impact in zip(oos_trades.iterrows(), oos_equity_impact):
                result.oos_trades.append(
                    TradeRecord(
                        asset=asset,
                        timeframe=timeframe,
                        entry_time=str(row["Entry Timestamp"]),
                        return_pct=float(row["Return"]),
                        equity_impact_pct=float(impact),
                    )
                )
        cursor = oos_end

    return result


def compute_summary_metrics(trades: list[TradeRecord]) -> dict:
    """Métricas primarias de la sección 3.3, calculadas SOLO sobre trades
    out-of-sample concatenados, en términos de EQUITY real (sizing por
    riesgo fijo fraccional, no del retorno bruto del instrumento).

    La curva de equity se construye ordenando los trades cronológicamente
    por `entry_time` y componiendo secuencialmente. Simplificación
    documentada: asume una posición a la vez en el tiempo (el sistema real
    permite hasta `MAX_POSITIONS=3` concurrentes) — esto SERIALIZA trades
    que en producción podrían solaparse, lo cual es conservador (subestima
    el uso de capital, no lo sobreestima)."""
    if not trades:
        return {
            "n_trades": 0,
            "win_rate": None,
            "avg_win_equity_pct": None,
            "avg_loss_equity_pct": None,
            "expectancy_per_trade_equity_pct": None,
            "profit_factor": None,
            "sharpe_simple": None,
            "max_drawdown_pct": None,
            "compounded_return_pct": None,
        }

    ordered = sorted(trades, key=lambda t: t.entry_time)
    returns = pd.Series([t.equity_impact_pct for t in ordered])

    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    win_rate = len(wins) / len(returns)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)

    mean_r = float(returns.mean())
    std_r = float(returns.std())
    sharpe_simple = (mean_r / std_r) if std_r > 0 else None

    equity = (1 + returns).cumprod()
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    max_drawdown_pct = float(drawdown.min())
    compounded_return_pct = float(equity.iloc[-1] - 1)

    return {
        "n_trades": len(returns),
        "win_rate": win_rate,
        "avg_win_equity_pct": avg_win,
        "avg_loss_equity_pct": avg_loss,
        "expectancy_per_trade_equity_pct": expectancy,
        "profit_factor": profit_factor,
        "sharpe_simple": sharpe_simple,
        "max_drawdown_pct": max_drawdown_pct,
        "compounded_return_pct": compounded_return_pct,
    }


async def run_full_walk_forward() -> dict:
    settings = get_settings()
    risk_per_trade = float(settings.risk_per_trade)
    assets = list(settings.universe_list)
    if "BTCUSDT" not in assets:
        assets.append("BTCUSDT")

    pooled = WalkForwardResult()
    async with get_session() as session:
        for asset in assets:
            for timeframe in TIMEFRAMES:
                candles = await get_all_candles(session, asset, timeframe)
                df = _candles_to_indexed_frame(candles)
                fold_result = walk_forward_asset(df, asset, timeframe, risk_per_trade, settings)
                pooled.folds.extend(fold_result.folds)
                pooled.oos_trades.extend(fold_result.oos_trades)

        btc_1h_candles = await get_all_candles(session, "BTCUSDT", "1h")

    btc_df = _candles_to_indexed_frame(btc_1h_candles)
    btc_return_pct = (
        float(btc_df["close"].iloc[-1] / btc_df["close"].iloc[0] - 1) if len(btc_df) else None
    )

    return {
        "git_sha": get_git_sha(),
        "generated_at": datetime.now().isoformat(),
        "config": {
            "lookback_grid": LOOKBACK_GRID,
            "volume_mult_grid": VOLUME_MULT_GRID,
            "in_sample_months": IN_SAMPLE_MONTHS,
            "out_sample_months": OUT_SAMPLE_MONTHS,
            "fees": str(settings.taker_fee),
            "slippage": DEFAULT_SLIPPAGE,
            "risk_per_trade": risk_per_trade,
            "universe": assets,
            "timeframes": list(TIMEFRAMES),
        },
        "folds": [asdict(f) for f in pooled.folds],
        "summary": compute_summary_metrics(pooled.oos_trades),
        "buy_and_hold_btc": {
            "period_start": str(btc_df.index[0]) if len(btc_df) else None,
            "period_end": str(btc_df.index[-1]) if len(btc_df) else None,
            "return_pct": btc_return_pct,
        },
    }


if __name__ == "__main__":
    output = asyncio.run(run_full_walk_forward())
    print(json.dumps(output, indent=2, default=str))
