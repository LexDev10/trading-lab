"""`decide_final_action` — el `policy_outcome` opcional (fase 3, sección
13) no debe cambiar el comportamiento anterior cuando no se pasa
(compatibilidad con `technical_only`, donde `evaluate_asset` nunca lo
calcula)."""

from datetime import UTC, datetime
from decimal import Decimal

from core.enums import HorizonClass, Regime, SignalDirection, TechnicalConviction
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from services.decision.policy import PolicyOutcome
from services.scanner.scanner import decide_final_action


def _signal() -> TechnicalSignal:
    return TechnicalSignal(
        asset="SOLUSDT",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        candle_close_time=datetime(2026, 1, 1, tzinfo=UTC),
        timeframe="1h",
        regime=Regime.range,
        direction=SignalDirection.long,
        conviction=TechnicalConviction.strong,
        setup_type="range_breakout",
        entry_zone=(Decimal("100"), Decimal("101")),
        stop_loss=Decimal("95"),
        take_profit=Decimal("115"),
        atr_14=Decimal("2"),
        rel_volume=Decimal("2"),
        horizon_class=HorizonClass.hours,
        invalidation_rule="close_1h_below_100",
        invalidation_level=Decimal("100"),
        evidence={},
    )


def _approved_verdict() -> RiskVerdict:
    return RiskVerdict(
        approved=True, rejection_reasons=[], checks={}, size_quote=Decimal("100"),
        rr_net_of_fees=Decimal("2"), portfolio_snapshot={},
    )


def test_informe_mode_never_enters_regardless_of_policy():
    action, would_enter = decide_final_action("informe", _signal(), _approved_verdict())
    assert would_enter is False


def test_no_technical_signal_rejects():
    action, would_enter = decide_final_action("operate", None, _approved_verdict())
    assert action.value == "reject"
    assert would_enter is False


def test_risk_not_approved_rejects_even_with_policy_enter():
    unapproved = _approved_verdict().model_copy(update={"approved": False})
    policy = PolicyOutcome("enter", Decimal("1.0"))
    action, would_enter = decide_final_action("operate", _signal(), unapproved, policy)
    assert action.value == "reject"
    assert would_enter is False


def test_no_policy_outcome_preserves_pre_fase3_behavior():
    action, would_enter = decide_final_action("operate", _signal(), _approved_verdict())
    assert would_enter is True
    assert action.value == "watchlist"  # sección 21: sin executor, would_enter=True lo convierte en "enter"


def test_policy_reject_overrides_approved_risk_verdict():
    policy = PolicyOutcome("reject", Decimal("0"))
    action, would_enter = decide_final_action("operate", _signal(), _approved_verdict(), policy)
    assert action.value == "reject"
    assert would_enter is False


def test_policy_watchlist_blocks_entry_despite_approved_risk_verdict():
    policy = PolicyOutcome("watchlist", Decimal("0"))
    action, would_enter = decide_final_action("operate", _signal(), _approved_verdict(), policy)
    assert action.value == "watchlist"
    assert would_enter is False


def test_policy_enter_behaves_like_no_policy():
    policy = PolicyOutcome("enter", Decimal("0.5"))
    action, would_enter = decide_final_action("operate", _signal(), _approved_verdict(), policy)
    assert would_enter is True
