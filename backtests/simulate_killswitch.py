"""Simula el efecto del `drawdown_killswitch` (app/config.py, 10% por
defecto) sobre la MISMA lista de trades OOS que produce
`backtests/walk_forward.py` — no recalcula señales ni parámetros, solo
añade una regla más al recorrido secuencial de la curva de equity: si en
el momento de considerar un trade nuevo el drawdown acumulado (pico vs.
equity actual) ya es >= `drawdown_killswitch`, ese trade se descarta (no
se abre), exactamente como haría `checks["drawdown_killswitch"]` del risk
engine real (`services/risk/engine.py`) con nuevas entradas.

Limitación explícita (honesta, no oculta): el check real es un gate
LIVE que se re-evalúa en cada entrada y se auto-desbloquea en cuanto el
equity vuelve a subir por encima del 90% del pico — pero el equity de
ESTE backtest solo se mueve por trades que SÍ se ejecutan (curva
secuencial de una sola posición a la vez, misma simplificación
documentada en `backtests/RESULTS.md`). Si no se permiten nuevas
entradas, no hay forma de que el equity mejore, así que una vez
disparado el freno, esta simulación no puede recuperarse por sí sola —
a diferencia del sistema real, que permite hasta 3 posiciones
concurrentes: otras posiciones abiertas ANTES del freno en otros activos
sí podrían seguir cerrando y recuperar equity sin que este backtest de
una sola posición pueda representarlo. Este script muestra el escenario
más pesimista posible del freno, no una predicción de lo que habría
pasado en producción.

Uso (con el stack levantado, tras walk_forward ya haber corrido al menos
una vez para confirmar que hay velas suficientes):
    docker compose exec app uv run python -m backtests.simulate_killswitch
"""

import asyncio
import json
from dataclasses import asdict
from datetime import datetime

from app.config import get_settings
from backtests.walk_forward import (
    TIMEFRAMES,
    TradeRecord,
    _candles_to_indexed_frame,
    compute_summary_metrics,
    walk_forward_asset,
)
from core.git_info import get_git_sha
from db.session import get_session
from services.data.persistence import get_all_candles


def _apply_killswitch(trades: list[TradeRecord], threshold: float) -> tuple[list[TradeRecord], list[TradeRecord], str | None]:
    """Recorre los trades en orden cronológico de entrada componiendo
    equity. Antes de CADA trade, si el drawdown acumulado (pico vs.
    equity actual, mismo cálculo que `_get_drawdown_pct`) ya es >=
    threshold, el trade se descarta (no se abre) — igual que
    `checks["drawdown_killswitch"]` bloquearía la entrada en el risk
    engine real. Devuelve (aplicados, descartados, timestamp del primer
    disparo o None si nunca se dispara)."""
    ordered = sorted(trades, key=lambda t: t.entry_time)

    applied: list[TradeRecord] = []
    skipped: list[TradeRecord] = []
    equity = 1.0
    peak = 1.0
    first_trigger: str | None = None

    for trade in ordered:
        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        if drawdown >= threshold:
            if first_trigger is None:
                first_trigger = trade.entry_time
            skipped.append(trade)
            continue
        applied.append(trade)
        equity *= 1 + trade.equity_impact_pct
        peak = max(peak, equity)

    return applied, skipped, first_trigger


async def main() -> None:
    settings = get_settings()
    threshold = float(settings.drawdown_killswitch)
    risk_per_trade = float(settings.risk_per_trade)
    assets = list(settings.universe_list)
    if "BTCUSDT" not in assets:
        assets.append("BTCUSDT")

    pooled_trades: list[TradeRecord] = []
    async with get_session() as session:
        for asset in assets:
            for timeframe in TIMEFRAMES:
                candles = await get_all_candles(session, asset, timeframe)
                df = _candles_to_indexed_frame(candles)
                fold_result = walk_forward_asset(df, asset, timeframe, risk_per_trade, settings)
                pooled_trades.extend(fold_result.oos_trades)

    applied, skipped, first_trigger = _apply_killswitch(pooled_trades, threshold)

    output = {
        "git_sha": get_git_sha(),
        "generated_at": datetime.now().isoformat(),
        "drawdown_killswitch_threshold": threshold,
        "note": (
            "Simulacion PESIMISTA del freno: una vez disparado, esta curva de "
            "una sola posicion no puede recuperarse (no se permiten nuevas "
            "entradas y el equity solo se mueve con trades aplicados). El "
            "sistema real (hasta 3 posiciones concurrentes) probablemente "
            "recuperaria via otras posiciones ya abiertas en otros activos, "
            "algo que este modelo no puede representar."
        ),
        "total_oos_trades": len(pooled_trades),
        "trades_applied": len(applied),
        "trades_skipped_after_freeze": len(skipped),
        "first_freeze_at": first_trigger,
        "summary_with_killswitch": compute_summary_metrics(applied),
        "summary_without_killswitch_for_reference": compute_summary_metrics(pooled_trades),
    }
    print(json.dumps(output, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
