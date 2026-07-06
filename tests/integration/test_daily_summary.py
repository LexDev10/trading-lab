"""Integration test contra Postgres real: `build_daily_summary` agrega
equity/drawdown (`portfolio_state`), trades del día (`trade_exits`) y
rechazos por motivo (`decision_logs.rejection_reasons`) — sección 17.

`equity_snapshots` no tiene FK a nada (es un rollup puntual, sección 16),
así que "última equity" se decide por la última fila insertada (`id`, ver
FIX 2026-07-06 en `portfolio_state.py`; antes era por `ts`) sobre TODA la
tabla `environment='paper'`. Para que la fila de este test sea de verdad
"la última" — sin importar qué datos reales de paper trading existan ya
en la DB — se siembra con `ts = now()` real (capturado al arrancar el
test), no una fecha fija en el pasado (eso rompería el día que exista una
posición de papel real cerrada con timestamp posterior). La limpieza
acota `equity_snapshots` a una ventana de `ts` estrecha alrededor de ese
`now`, mismo criterio que `test_paper_ledger.py` pero con ventana en vez
de fecha fija.

Los rechazos/trades del día son agregados GLOBALES (sección 17: el
resumen es de todo el universo, no solo del activo de prueba) — este
test comparte Postgres con la app real corriendo en paralelo (scheduler
en vivo, ya generó actividad real durante esta sesión), así que se
afirma sobre el DELTA introducido por los datos sembrados, no sobre
valores absolutos."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from core.enums import FinalAction, Trigger
from db.models import DecisionLog, EquitySnapshot, TradeEntry, TradeExit
from db.session import get_session
from services.reporting.daily_summary import _rejections_today, _trades_today, build_daily_summary

pytestmark = pytest.mark.integration

TEST_ASSET = "ZZSUMMARYUSDT"
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
        await session.execute(delete(DecisionLog).where(DecisionLog.asset == TEST_ASSET))
        await session.execute(
            delete(EquitySnapshot).where(
                EquitySnapshot.environment == "paper",
                EquitySnapshot.ts >= now - EQUITY_WINDOW,
                EquitySnapshot.ts <= now + EQUITY_WINDOW,
            )
        )
        await session.commit()


async def _seed_decision_log(session, now: datetime, rejection_reasons: list[str]) -> int:
    row = DecisionLog(
        ts=now,
        mode="technical_only",
        trigger=Trigger.scheduled.value,
        asset=TEST_ASSET,
        git_sha="test",
        scanner_jsonb={},
        final_action=FinalAction.reject.value,
        rejection_reasons=rejection_reasons,
    )
    session.add(row)
    await session.flush()
    return row.id


@pytest.mark.asyncio
async def test_build_daily_summary_aggregates_rejections_and_trades():
    now = datetime.now(tz=UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with get_session() as session:
        baseline_rejections = dict(await _rejections_today(session, day_start))
        baseline_trades_count, baseline_trades_pnl = await _trades_today(session, day_start)

    async with get_session() as session:
        await _seed_decision_log(session, now, ["liquidity"])
        await _seed_decision_log(session, now, ["liquidity", "spread"])
        enter_decision_log_id = await _seed_decision_log(session, now, [])

        entry = TradeEntry(
            decision_log_id=enter_decision_log_id,
            asset=TEST_ASSET,
            environment="paper",
            client_order_id=f"paper-summary-test-{enter_decision_log_id}",
            entry_time=now,
            entry_price=Decimal("100"),
            qty=Decimal("10"),
            tp=Decimal("110"),
            sl=Decimal("90"),
            status="closed_tp",
        )
        session.add(entry)
        await session.flush()

        session.add(
            TradeExit(
                trade_entry_id=entry.id,
                exit_time=now,
                exit_price=Decimal("110"),
                exit_qty=Decimal("10"),
                exit_type="closed_tp",
                fees_paid=Decimal("2.1"),
                pnl_quote=Decimal("50"),
                pnl_pct_net=Decimal("0.05"),
            )
        )
        session.add(
            EquitySnapshot(
                ts=now,
                environment="paper",
                equity_quote=Decimal("10050"),
                open_positions=0,
                drawdown_pct=Decimal("0"),
            )
        )
        await session.commit()

    async with get_session() as session:
        settings = get_settings()
        text = await build_daily_summary(session, settings, now=now)

    expected_liquidity = baseline_rejections.get("liquidity", 0) + 2
    expected_spread = baseline_rejections.get("spread", 0) + 1
    expected_pnl = baseline_trades_pnl + Decimal("50")
    assert f"liquidity: {expected_liquidity}" in text
    assert f"spread: {expected_spread}" in text
    assert f"Trades cerrados hoy: {baseline_trades_count + 1}" in text
    assert f"pnl total={expected_pnl:.4f} USDT" in text
    assert "10050.00" in text
