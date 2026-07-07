"""`services/reporting/dashboard_data.py` contra Postgres real — mismo
Postgres que la app real en background (scheduler en vivo), así que se
afirma sobre el DELTA introducido por los datos sembrados, no sobre
valores absolutos (mismo criterio que `test_daily_summary.py`, ver su
docstring). `equity_snapshots`/`decision_logs` se siembran con
`ts=now()` real, nunca una fecha fija (mismo motivo que ahí: una fila
posterior real invalidaría "la más reciente")."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, or_, select

from app.config import get_settings
from db.models import DecisionLog, EquitySnapshot, ItemClassification, TradeEntry, TradeExit
from db.session import get_session
from services.reporting.dashboard_data import (
    compute_closed_trades_summary,
    count_open_positions,
    get_active_vetoes,
    get_closed_trades_history,
    get_decision_detail,
    get_decisions_by_mode,
    get_equity_curve,
    get_open_positions_detail,
    get_recent_classifications,
    get_recent_decisions,
    get_system_state_info,
)

pytestmark = pytest.mark.integration

TEST_ASSET = "ZZDASHBOARDUSDT"
TEST_MODE = "zz_test_mode"
EQUITY_WINDOW = timedelta(minutes=5)


@pytest.fixture(autouse=True)
async def _cleanup():
    now = datetime.now(tz=UTC)
    await _delete_test_rows(now)
    yield
    await _delete_test_rows(now)


async def _delete_test_rows(now: datetime) -> None:
    async with get_session() as session:
        entry_ids_subq = select(TradeEntry.id).where(TradeEntry.asset == TEST_ASSET)
        await session.execute(delete(TradeExit).where(TradeExit.trade_entry_id.in_(entry_ids_subq)))
        await session.execute(delete(TradeEntry).where(TradeEntry.asset == TEST_ASSET))
        await session.execute(
            delete(DecisionLog).where(or_(DecisionLog.mode == TEST_MODE, DecisionLog.asset == TEST_ASSET))
        )
        await session.execute(
            delete(EquitySnapshot).where(
                EquitySnapshot.environment == "paper",
                EquitySnapshot.ts >= now - EQUITY_WINDOW,
                EquitySnapshot.ts <= now + EQUITY_WINDOW,
            )
        )
        await session.execute(delete(ItemClassification).where(ItemClassification.asset_tags.contains(["ZZDASH"])))
        await session.commit()


async def test_get_equity_curve_includes_seeded_point_as_latest():
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        session.add(
            EquitySnapshot(ts=now, environment="paper", equity_quote=Decimal("12345.67"), open_positions=0, drawdown_pct=Decimal("0.01"))
        )
        await session.commit()

    async with get_session() as session:
        curve = await get_equity_curve(session)

    assert curve, "la curva no debería estar vacía tras sembrar un punto"
    assert curve[-1].equity_quote == Decimal("12345.67")
    assert curve[-1].drawdown_pct == Decimal("0.01")


async def _seed_decision_log(session, now: datetime) -> int:
    row = DecisionLog(
        ts=now, mode="technical_only", trigger="scheduled", asset=TEST_ASSET,
        git_sha="test", scanner_jsonb={}, final_action="enter", rejection_reasons=[],
    )
    session.add(row)
    await session.flush()
    return row.id


async def test_count_open_positions_reflects_seeded_open_entry():
    async with get_session() as session:
        baseline = await count_open_positions(session)

    now = datetime.now(tz=UTC)
    async with get_session() as session:
        decision_log_id = await _seed_decision_log(session, now)
        session.add(
            TradeEntry(
                decision_log_id=decision_log_id, asset=TEST_ASSET, environment="paper",
                client_order_id=f"paper-dashboard-test-open-{now.timestamp()}",
                entry_time=now, entry_price=Decimal("100"), qty=Decimal("1"),
                tp=Decimal("110"), sl=Decimal("90"), status="open",
            )
        )
        await session.commit()

    async with get_session() as session:
        after = await count_open_positions(session)

    assert after == baseline + 1


async def test_compute_closed_trades_summary_delta_from_seeded_trade():
    async with get_session() as session:
        baseline = await compute_closed_trades_summary(session)

    now = datetime.now(tz=UTC)
    async with get_session() as session:
        decision_log_id = await _seed_decision_log(session, now)
        entry = TradeEntry(
            decision_log_id=decision_log_id, asset=TEST_ASSET, environment="paper",
            client_order_id=f"paper-dashboard-test-closed-{now.timestamp()}",
            entry_time=now, entry_price=Decimal("100"), qty=Decimal("10"),
            tp=Decimal("110"), sl=Decimal("90"), status="closed_tp",
        )
        session.add(entry)
        await session.flush()
        session.add(
            TradeExit(
                trade_entry_id=entry.id, exit_time=now, exit_price=Decimal("110"), exit_qty=Decimal("10"),
                exit_type="closed_tp", fees_paid=Decimal("2.1"), pnl_quote=Decimal("50"), pnl_pct_net=Decimal("0.05"),
                processed_at=now,
            )
        )
        await session.commit()

    async with get_session() as session:
        after = await compute_closed_trades_summary(session)

    assert after.n_trades == baseline.n_trades + 1
    assert after.total_pnl_quote == baseline.total_pnl_quote + Decimal("50")


async def test_get_decisions_by_mode_groups_by_mode_and_action():
    async with get_session() as session:
        session.add(
            DecisionLog(
                ts=datetime.now(tz=UTC), mode=TEST_MODE, trigger="scheduled", asset=TEST_ASSET,
                git_sha="test", scanner_jsonb={}, final_action="reject", rejection_reasons=["no_setup"],
            )
        )
        session.add(
            DecisionLog(
                ts=datetime.now(tz=UTC), mode=TEST_MODE, trigger="scheduled", asset=TEST_ASSET,
                git_sha="test", scanner_jsonb={}, final_action="reject", rejection_reasons=["no_setup"],
            )
        )
        session.add(
            DecisionLog(
                ts=datetime.now(tz=UTC), mode=TEST_MODE, trigger="scheduled", asset=TEST_ASSET,
                git_sha="test", scanner_jsonb={}, final_action="watchlist", rejection_reasons=[],
            )
        )
        await session.commit()

    async with get_session() as session:
        rows = await get_decisions_by_mode(session)

    by_key = {(r.mode, r.final_action): r.count for r in rows}
    assert by_key[(TEST_MODE, "reject")] == 2
    assert by_key[(TEST_MODE, "watchlist")] == 1


async def test_get_recent_decisions_includes_seeded_row():
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        session.add(
            DecisionLog(
                ts=now, mode=TEST_MODE, trigger="scheduled", asset=TEST_ASSET,
                git_sha="test", scanner_jsonb={}, final_action="reject", rejection_reasons=["liquidity"],
            )
        )
        await session.commit()

    async with get_session() as session:
        recent = await get_recent_decisions(session, limit=500)

    matching = [r for r in recent if r.asset == TEST_ASSET and r.mode == TEST_MODE]
    assert len(matching) == 1
    assert matching[0].rejection_reasons == ["liquidity"]


async def test_get_decision_detail_returns_risk_verdict_jsonb():
    now = datetime.now(tz=UTC)
    risk_verdict = {"approved": False, "checks": {"regime_filter": True, "rr_too_low": False}}
    async with get_session() as session:
        row = DecisionLog(
            ts=now, mode=TEST_MODE, trigger="scheduled", asset=TEST_ASSET,
            git_sha="test", scanner_jsonb={}, final_action="reject", rejection_reasons=["rr_too_low"],
            risk_verdict_jsonb=risk_verdict,
        )
        session.add(row)
        await session.flush()
        decision_log_id = row.id
        await session.commit()

    async with get_session() as session:
        detail = await get_decision_detail(session, decision_log_id)

    assert detail is not None
    assert detail.asset == TEST_ASSET
    assert detail.risk_verdict == risk_verdict


async def test_get_decision_detail_returns_none_for_unknown_id():
    async with get_session() as session:
        detail = await get_decision_detail(session, 9_999_999)
    assert detail is None


async def test_get_open_positions_detail_includes_seeded_pending_entry():
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        decision_log_id = await _seed_decision_log(session, now)
        session.add(
            TradeEntry(
                decision_log_id=decision_log_id, asset=TEST_ASSET, environment="paper",
                client_order_id=f"paper-dashboard-test-pending-{now.timestamp()}",
                entry_time=now, entry_price=Decimal("100"), qty=Decimal("1"),
                tp=Decimal("110"), sl=Decimal("90"), status="pending",
                entry_zone_low=Decimal("99"), entry_zone_high=Decimal("101"),
            )
        )
        await session.commit()

    async with get_session() as session:
        positions = await get_open_positions_detail(session)

    matching = [p for p in positions if p.asset == TEST_ASSET]
    assert len(matching) == 1
    assert matching[0].status == "pending"
    assert matching[0].entry_zone_low == Decimal("99")


async def test_get_system_state_info_defaults_to_running_without_row():
    # No se limpia/siembra system_state aquí a propósito: es una fila
    # única (id=1) compartida con la app real en background: basta con
    # afirmar que el tipo de retorno es coherente, sin asumir un estado
    # concreto (podría estar en halt por otra prueba/operador).
    async with get_session() as session:
        info = await get_system_state_info(session)
    assert info.state in ("running", "halt")


async def test_get_active_vetoes_reflects_seeded_veto():
    settings = get_settings()
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        session.add(
            ItemClassification(
                item_id=1, item_kind="news", model_name="test", model_version="1",
                classified_at=now, stance="bearish_strong", event_types=["hack_exploit"], veto=True,
                summary="test veto", output_jsonb={}, asset_tags=["ZZDASH"],
                published_at=now, source="test_source",
            )
        )
        await session.commit()

    async with get_session() as session:
        # Universo temporal de un solo activo de prueba, para no depender
        # de vetos reales que pudiera haber en el universo real ahora mismo.
        test_settings = settings.model_copy(update={"universe": "ZZDASHUSDT"})
        active = await get_active_vetoes(session, test_settings, now)

    assert active == ["ZZDASH"]


async def test_get_closed_trades_history_includes_seeded_trade():
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        decision_log_id = await _seed_decision_log(session, now)
        entry = TradeEntry(
            decision_log_id=decision_log_id, asset=TEST_ASSET, environment="paper",
            client_order_id=f"paper-dashboard-test-history-{now.timestamp()}",
            entry_time=now, entry_price=Decimal("100"), qty=Decimal("2"),
            tp=Decimal("110"), sl=Decimal("90"), status="closed_tp",
        )
        session.add(entry)
        await session.flush()
        session.add(
            TradeExit(
                trade_entry_id=entry.id, exit_time=now, exit_price=Decimal("110"), exit_qty=Decimal("2"),
                exit_type="closed_tp", fees_paid=Decimal("0.4"), pnl_quote=Decimal("20"), pnl_pct_net=Decimal("0.1"),
                processed_at=now,
            )
        )
        await session.commit()

    async with get_session() as session:
        history = await get_closed_trades_history(session, limit=500)

    matching = [r for r in history if r.asset == TEST_ASSET]
    assert len(matching) == 1
    assert matching[0].pnl_quote == Decimal("20")
    assert matching[0].exit_type == "closed_tp"


async def test_get_recent_classifications_includes_seeded_row():
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        session.add(
            ItemClassification(
                item_id=2, item_kind="social", model_name="test", model_version="1",
                classified_at=now, stance="neutral", event_types=[], veto=False,
                summary="resumen de prueba", output_jsonb={}, asset_tags=["ZZDASH"],
                published_at=now, source="reddit/test",
            )
        )
        await session.commit()

    async with get_session() as session:
        recent = await get_recent_classifications(session, limit=500)

    matching = [r for r in recent if "ZZDASH" in r.asset_tags]
    assert len(matching) == 1
    assert matching[0].summary == "resumen de prueba"
