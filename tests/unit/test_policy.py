"""Meta-decider (sección 13) — un caso por fila de `POLICY_TABLE`, mismo
criterio que los checks parametrizados del risk engine (sección 18)."""

from decimal import Decimal

import pytest

from core.enums import FundamentalStance, TechnicalConviction
from services.decision.policy import apply_fundamental_policy, evaluate_policy


@pytest.mark.parametrize(
    "conviction,stance,expected_action,expected_multiplier",
    [
        (TechnicalConviction.strong, FundamentalStance.bullish_strong, "enter", Decimal("1.0")),
        (TechnicalConviction.strong, FundamentalStance.bullish_weak, "enter", Decimal("1.0")),
        (TechnicalConviction.strong, FundamentalStance.neutral, "enter", Decimal("1.0")),
        (TechnicalConviction.strong, FundamentalStance.unknown, "enter", Decimal("1.0")),
        (TechnicalConviction.strong, FundamentalStance.bearish_weak, "enter", Decimal("0.5")),
        (TechnicalConviction.strong, FundamentalStance.bearish_strong, "reject", Decimal("0")),
        (TechnicalConviction.moderate, FundamentalStance.bullish_strong, "enter", Decimal("1.0")),
        (TechnicalConviction.moderate, FundamentalStance.bullish_weak, "watchlist", Decimal("0")),
        (TechnicalConviction.moderate, FundamentalStance.neutral, "watchlist", Decimal("0")),
        (TechnicalConviction.moderate, FundamentalStance.unknown, "watchlist", Decimal("0")),
        (TechnicalConviction.moderate, FundamentalStance.bearish_weak, "watchlist", Decimal("0")),
        (TechnicalConviction.moderate, FundamentalStance.bearish_strong, "watchlist", Decimal("0")),
        (TechnicalConviction.weak, FundamentalStance.bullish_strong, "reject", Decimal("0")),
        (TechnicalConviction.weak, FundamentalStance.unknown, "reject", Decimal("0")),
    ],
)
def test_evaluate_policy_matches_table(conviction, stance, expected_action, expected_multiplier):
    outcome = evaluate_policy(conviction, stance)
    assert outcome.action == expected_action
    assert outcome.size_multiplier == expected_multiplier


def test_apply_fundamental_policy_none_in_technical_only_mode():
    outcome = apply_fundamental_policy(
        "technical_only", TechnicalConviction.strong, True, FundamentalStance.bearish_strong
    )
    assert outcome is None


def test_apply_fundamental_policy_none_when_risk_not_approved():
    outcome = apply_fundamental_policy(
        "technical_plus_fundamental", TechnicalConviction.strong, False, FundamentalStance.bullish_strong
    )
    assert outcome is None


def test_apply_fundamental_policy_consults_table_when_approved_and_not_technical_only():
    outcome = apply_fundamental_policy(
        "technical_plus_fundamental", TechnicalConviction.strong, True, FundamentalStance.bearish_weak
    )
    assert outcome is not None
    assert outcome.action == "enter"
    assert outcome.size_multiplier == Decimal("0.5")


def test_apply_fundamental_policy_applies_in_full_mode_too():
    outcome = apply_fundamental_policy("full", TechnicalConviction.weak, True, FundamentalStance.unknown)
    assert outcome is not None
    assert outcome.action == "reject"
