"""Risk engine determinista — sección 9. Ejecuta TODOS los checks siempre
(no cortocircuita) para que `RiskVerdict.checks` refleje el resultado de
cada uno, se apruebe o no la operación."""

from dataclasses import dataclass
from decimal import Decimal

from app.config import Settings
from core.enums import RejectionReason
from core.schemas.risk import RiskVerdict
from services.risk.portfolio_state import PortfolioSnapshot
from services.risk.sizing import calc_size_quote


@dataclass
class RiskInput:
    asset: str
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    atr_14: Decimal
    spread_bps: Decimal
    min_notional: Decimal
    regime_blocks_entries: bool  # sección 8.3: trend_down/chop_high_vol de BTC


def evaluate_risk(
    risk_input: RiskInput, portfolio: PortfolioSnapshot, settings: Settings
) -> RiskVerdict:
    checks: dict[str, bool] = {}
    reasons: list[RejectionReason] = []

    # Filtro de régimen de cartera (sección 8.3) — se trata como un check
    # más del engine para que quede en `checks` y no se salte nunca, ni
    # siquiera en modo manual (sección 21.2: "no existe ningún comando de
    # override").
    regime_ok = not risk_input.regime_blocks_entries
    checks["regime_filter"] = regime_ok
    if not regime_ok:
        reasons.append(RejectionReason.regime_filter)

    entry = risk_input.entry
    stop_loss = risk_input.stop_loss
    take_profit = risk_input.take_profit
    atr_14 = risk_input.atr_14

    # --- 9.1 checks por operación ---

    # rr_net: R:R neto de comisiones >= MIN_RR_NET.
    # DECISION: fees expresados en términos de precio (round-trip = 2 x
    # taker_fee del notional, aproximado como fracción del precio de
    # entrada) se restan del recorrido a favor y se suman al recorrido en
    # contra, porque se pagan se gane o se pierda el trade.
    fee_price = (settings.taker_fee * Decimal("2")) * entry
    net_gain = (take_profit - entry) - fee_price
    net_loss = (entry - stop_loss) + fee_price
    rr_net = (net_gain / net_loss) if net_loss > 0 else None
    rr_net_ok = rr_net is not None and rr_net >= settings.min_rr_net
    checks["rr_net"] = rr_net_ok
    if not rr_net_ok:
        reasons.append(RejectionReason.rr_too_low)

    # sl_distance_min / sl_distance_max
    sl_distance = entry - stop_loss
    sl_min_ok = atr_14 > 0 and sl_distance >= (Decimal("1.0") * atr_14)
    sl_max_ok = atr_14 > 0 and sl_distance <= (Decimal("4.0") * atr_14)
    checks["sl_distance_min"] = sl_min_ok
    checks["sl_distance_max"] = sl_max_ok
    if not sl_min_ok or not sl_max_ok:
        reasons.append(RejectionReason.sl_distance_invalid)

    # spread
    spread_ok = risk_input.spread_bps <= settings.max_spread_bps
    checks["spread"] = spread_ok
    if not spread_ok:
        reasons.append(RejectionReason.spread)

    # notional_min (tamaño calculado antes de redondeo a filtros de exchange,
    # que ocurre en binance_executor.py, sección 10.1)
    size_quote = calc_size_quote(portfolio.equity_quote, settings.risk_per_trade, entry, stop_loss)
    notional_ok = size_quote >= (risk_input.min_notional * Decimal("1.2"))
    checks["notional_min"] = notional_ok
    if not notional_ok:
        reasons.append(RejectionReason.exchange_filter)

    # --- 9.2 checks de cartera ---

    max_positions_ok = portfolio.open_positions < settings.max_positions
    checks["max_positions"] = max_positions_ok
    if not max_positions_ok:
        reasons.append(RejectionReason.max_positions)

    exposure_total_pct = (
        (portfolio.exposure_total_quote + size_quote) / portfolio.equity_quote
        if portfolio.equity_quote > 0
        else Decimal("1")
    )
    # DECISION: no existe un RejectionReason específico para
    # `max_exposure_total`; se reutiliza `max_positions` (misma familia de
    # límites de cartera por conteo/tamaño). El detalle exacto del check que
    # falló siempre está en `checks`, que se persiste completo.
    max_exposure_ok = exposure_total_pct <= settings.max_exposure_total
    checks["max_exposure_total"] = max_exposure_ok
    if not max_exposure_ok:
        reasons.append(RejectionReason.max_positions)

    exposure_btc_beta_pct = (
        (portfolio.exposure_btc_beta_quote + size_quote) / portfolio.equity_quote
        if portfolio.equity_quote > 0
        else Decimal("1")
    )
    correlated_exposure_ok = exposure_btc_beta_pct <= settings.max_exposure_btc_beta
    checks["correlated_exposure"] = correlated_exposure_ok
    if not correlated_exposure_ok:
        reasons.append(RejectionReason.correlated_exposure)

    daily_loss_ok = portfolio.daily_realized_pnl_pct > -settings.daily_loss_limit
    checks["daily_loss_limit"] = daily_loss_ok
    if not daily_loss_ok:
        reasons.append(RejectionReason.daily_loss_limit)

    drawdown_ok = portfolio.drawdown_pct < settings.drawdown_killswitch
    checks["drawdown_killswitch"] = drawdown_ok
    if not drawdown_ok:
        reasons.append(RejectionReason.drawdown_killswitch)

    # DECISION: `halt` (sección 9.2) se activa únicamente por el
    # drawdown_killswitch en el MVP, así que se reutiliza ese
    # RejectionReason; `checks["system_not_halted"]` deja explícito el
    # estado real leído de `system_state`.
    system_running_ok = portfolio.system_state == "running"
    checks["system_not_halted"] = system_running_ok
    if not system_running_ok:
        reasons.append(RejectionReason.drawdown_killswitch)

    cooldown_asset_ok = not portfolio.asset_cooldown_active
    checks["cooldown_asset"] = cooldown_asset_ok
    if not cooldown_asset_ok:
        reasons.append(RejectionReason.cooldown)

    cooldown_losses_ok = not portfolio.losses_cooldown_active
    checks["cooldown_losses"] = cooldown_losses_ok
    if not cooldown_losses_ok:
        reasons.append(RejectionReason.cooldown)

    approved = all(checks.values())

    return RiskVerdict(
        approved=approved,
        rejection_reasons=reasons,
        checks=checks,
        size_quote=size_quote if approved else None,
        rr_net_of_fees=rr_net,
        portfolio_snapshot=portfolio.raw,
    )
