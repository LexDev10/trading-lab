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

from db.models import DecisionLog, EquitySnapshot, TradeEntry, TradeExit
from db.session import get_session
from services.reporting.dashboard_data import (
    compute_closed_trades_summary,
    count_open_positions,
    get_decisions_by_mode,
    get_equity_curve,
    get_recent_decisions,
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
