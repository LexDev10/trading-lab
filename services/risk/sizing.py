"""Riesgo fijo fraccional — sección 9.3. Nunca sizing por convicción en el
MVP; el redondeo final a los filtros del exchange (tickSize/stepSize) es
responsabilidad de `binance_executor.py` (sección 10, fase de ejecución)."""

from decimal import Decimal


def calc_size_quote(
    equity_quote: Decimal, risk_per_trade: Decimal, entry: Decimal, stop_loss: Decimal
) -> Decimal:
    if entry <= 0 or stop_loss >= entry:
        return Decimal("0")
    relative_distance = (entry - stop_loss) / entry
    if relative_distance <= 0:
        return Decimal("0")
    return (equity_quote * risk_per_trade) / relative_distance
