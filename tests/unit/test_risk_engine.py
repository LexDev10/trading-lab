"""Risk engine — un caso que pasa y un caso que falla por cada check,
sección 18 ("tests parametrizados: uno que pasa y uno que falla por
check")."""

import dataclasses
from decimal import Decimal

import pytest

from app.config import Settings
from core.enums import RejectionReason
from services.risk.engine import RiskInput, evaluate_risk
from services.risk.portfolio_state import PortfolioSnapshot


def base_settings() -> Settings:
    return Settings(_env_file=None)


def base_risk_input() -> RiskInput:
    return RiskInput(
        asset="SOLUSDT",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("115"),
        atr_14=Decimal("4"),
        spread_bps=Decimal("3"),
        min_notional=Decimal("10"),
        regime_blocks_entries=False,
    )


def base_portfolio() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        equity_quote=Decimal("10000"),
        open_positions=0,
        exposure_total_quote=Decimal("0"),
        exposure_btc_beta_quote=Decimal("0"),
        daily_realized_pnl_pct=Decimal("0"),
        drawdown_pct=Decimal("0"),
        system_state="running",
        system_halted_reason=None,
        asset_cooldown_active=False,
        losses_cooldown_active=False,
        asset_has_open_position=False,
        raw={},
    )


def test_baseline_all_checks_pass_and_approved():
    verdict = evaluate_risk(base_risk_input(), base_portfolio(), base_settings())
    assert verdict.approved is True
    assert all(verdict.checks.values())
    assert verdict.rejection_reasons == []
    assert verdict.size_quote is not None


@pytest.mark.parametrize(
    "check_name,mutate_input,mutate_portfolio,expected_reason",
    [
        (
            "regime_filter",
            lambda ri: dataclasses.replace(ri, regime_blocks_entries=True),
            lambda p: p,
            RejectionReason.regime_filter,
        ),
        (
            "fundamental_veto",
            lambda ri: dataclasses.replace(ri, fundamental_veto_active=True),
            lambda p: p,
            RejectionReason.fundamental_veto,
        ),
        (
            "rr_net",
            lambda ri: dataclasses.replace(ri, take_profit=Decimal("103")),
            lambda p: p,
            RejectionReason.rr_too_low,
        ),
        (
            "sl_distance_min",
            lambda ri: dataclasses.replace(ri, atr_14=Decimal("10")),
            lambda p: p,
            RejectionReason.sl_distance_invalid,
        ),
        (
            "sl_distance_max",
            lambda ri: dataclasses.replace(ri, atr_14=Decimal("1")),
            lambda p: p,
            RejectionReason.sl_distance_invalid,
        ),
        (
            "spread",
            lambda ri: dataclasses.replace(ri, spread_bps=Decimal("10")),
            lambda p: p,
            RejectionReason.spread,
        ),
        (
            "notional_min",
            lambda ri: dataclasses.replace(ri, min_notional=Decimal("2000")),
            lambda p: p,
            RejectionReason.exchange_filter,
        ),
        (
            "no_open_position_same_asset",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, asset_has_open_position=True),
            RejectionReason.position_already_open,
        ),
        (
            "max_positions",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, open_positions=3),
            RejectionReason.max_positions,
        ),
        (
            "max_exposure_total",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, exposure_total_quote=Decimal("2900")),
            RejectionReason.max_positions,
        ),
        (
            "correlated_exposure",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, exposure_btc_beta_quote=Decimal("1100")),
            RejectionReason.correlated_exposure,
        ),
        (
            "daily_loss_limit",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, daily_realized_pnl_pct=Decimal("-0.03")),
            RejectionReason.daily_loss_limit,
        ),
        (
            "drawdown_killswitch",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, drawdown_pct=Decimal("0.15")),
            RejectionReason.drawdown_killswitch,
        ),
        (
            "system_not_halted",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, system_state="halt"),
            RejectionReason.drawdown_killswitch,
        ),
        (
            "cooldown_asset",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, asset_cooldown_active=True),
            RejectionReason.cooldown,
        ),
        (
            "cooldown_losses",
            lambda ri: ri,
            lambda p: dataclasses.replace(p, losses_cooldown_active=True),
            RejectionReason.cooldown,
        ),
    ],
)
def test_each_check_fails_in_isolation(check_name, mutate_input, mutate_portfolio, expected_reason):
    risk_input = mutate_input(base_risk_input())
    portfolio = mutate_portfolio(base_portfolio())

    verdict = evaluate_risk(risk_input, portfolio, base_settings())

    assert verdict.approved is False
    assert verdict.checks[check_name] is False
    assert expected_reason in verdict.rejection_reasons
    other_checks = {k: v for k, v in verdict.checks.items() if k != check_name}
    assert all(other_checks.values()), f"otros checks fallaron inesperadamente: {other_checks}"
