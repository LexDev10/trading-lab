"""Integration test contra Postgres real (mismo patrón que
`test_killswitch.py`): el paper ledger interno (`services/execution/
paper_ledger.py`) abre una posición de papel, la cierra al tocar TP contra
velas reales insertadas a mano, y las tablas que ya lee
`services/risk/portfolio_state.py` (`trade_entries`/`trade_exits`/
`equity_snapshots`) quedan consistentes sin ningún cambio ahí — ni una
sola llamada a Binance ni a ningún exchange en todo el test.

Las marcas de tiempo se anclan a `datetime.now(tz=UTC)` real (no a una
fecha fija en el pasado): `equity_snapshots` no tiene FK, así que "última
equity" se decide por el `ts` más reciente sobre TODA la tabla
`environment='paper'` — la app real corre en paralelo y ya genera
operaciones de papel reales (ver `docs/PHASE_2_REPORT.md`, hallazgo de la
sesión del 2026-07-03), así que una fecha fija en el pasado deja de ser
"la más reciente" en cuanto existe una sola operación real posterior.
Usar offsets relativos a `now()` garantiza que las filas de este test
siguen siendo las más recientes sin importar qué actividad real exista.

Requiere una base de datos Postgres real y alcanzable (DATABASE_URL). No
se ejecuta en el ciclo rápido de tests unitarios sin DB; correr con:

    docker compose exec app uv run pytest tests/integration -v
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.config import get_settings
from core.enums import FinalAction, HorizonClass, Regime, SignalDirection, TechnicalConviction, Trigger
from core.schemas.decision import DecisionRecord
from core.schemas.market import Candle
from core.schemas.risk import RiskVerdict
from core.schemas.technical import TechnicalSignal
from db.models import Candle as CandleRow
from db.models import DecisionLog, EquitySnapshot, PositionEvent, TradeEntry, TradeExit
from db.session import get_session
from journal.decision_logger import log_decision
from services.data.persistence import upsert_asset, upsert_candles
from services.execution.paper_ledger import ExitDecision, close_position, open_position, update_open_positions
from services.risk.portfolio_state import build_portfolio_snapshot, get_latest_equity

pytestmark = pytest.mark.integration

TEST_ASSET = "ZZTESTUSDT"
# Los tests usan offsets de hasta +3h sobre `now()` — ventana amplia para
# que la limpieza (antes y después de cada test) los cubra sin importar
# pequeños desfases entre el `now()` de la fixture y el del cuerpo del test.
EQUITY_WINDOW = timedelta(hours=6)


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        entry_ids_subq = select(TradeEntry.id).where(TradeEntry.asset == TEST_ASSET)
        await session.execute(delete(PositionEvent).where(PositionEvent.trade_entry_id.in_(entry_ids_subq)))
        await session.execute(delete(TradeExit).where(TradeExit.trade_entry_id.in_(entry_ids_subq)))
        await session.execute(delete(TradeEntry).where(TradeEntry.asset == TEST_ASSET))
        await session.execute(delete(DecisionLog).where(DecisionLog.asset == TEST_ASSET))
        await session.execute(delete(CandleRow).where(CandleRow.asset == TEST_ASSET))
        await session.execute(
            delete(EquitySnapshot).where(
                EquitySnapshot.environment == "paper",
                EquitySnapshot.ts >= now - EQUITY_WINDOW,
                EquitySnapshot.ts <= now + EQUITY_WINDOW,
            )
        )
        await session.commit()


def _technical_signal(now: datetime, **overrides) -> TechnicalSignal:
    defaults = dict(
        asset=TEST_ASSET,
        generated_at=now,
        candle_close_time=now,
        timeframe="1h",
        regime=Regime.trend_up,
        direction=SignalDirection.long,
        conviction=TechnicalConviction.moderate,
        setup_type="range_breakout",
        entry_zone=(Decimal("99"), Decimal("101")),
        stop_loss=Decimal("90"),
        take_profit=Decimal("110"),
        atr_14=Decimal("2"),
        rel_volume=Decimal("2"),
        horizon_class=HorizonClass.hours,
        invalidation_rule="close_1h_below_101",
        invalidation_level=Decimal("101"),
        evidence={},
    )
    defaults.update(overrides)
    return TechnicalSignal(**defaults)


def _approved_verdict(size_quote: Decimal = Decimal("1000")) -> RiskVerdict:
    return RiskVerdict(
        approved=True,
        rejection_reasons=[],
        checks={"ok": True},
        size_quote=size_quote,
        rr_net_of_fees=Decimal("2.0"),
        portfolio_snapshot={},
    )


async def _log_test_decision(session, now: datetime) -> int:
    record = DecisionRecord(
        ts=now,
        mode="technical_only",
        trigger=Trigger.manual,
        asset=TEST_ASSET,
        git_sha="test",
        scanner={},
        final_action=FinalAction.enter,
    )
    return await log_decision(session, record)


@pytest.mark.asyncio
async def test_open_and_close_position_updates_ledger_and_portfolio_state():
    settings = get_settings()
    now = datetime.now(tz=UTC)
    technical_signal = _technical_signal(now)
    verdict = _approved_verdict(Decimal("1000"))

    async with get_session() as session:
        decision_log_id = await _log_test_decision(session, now)
        entry = await open_position(
            session,
            settings,
            decision_log_id=decision_log_id,
            asset=TEST_ASSET,
            technical_signal=technical_signal,
            risk_verdict=verdict,
            entry_price=Decimal("100"),
            now=now,
        )
        await session.commit()
        entry_id = entry.id

    async with get_session() as session:
        equity_before = await get_latest_equity(session, settings)

    exit_time = now + timedelta(hours=3)
    exit_decision = ExitDecision(exit_time=exit_time, exit_price=Decimal("110"), exit_type="closed_tp")

    async with get_session() as session:
        entry = await session.get(TradeEntry, entry_id)
        await close_position(session, settings, entry, exit_decision)
        await session.commit()

    async with get_session() as session:
        entry = await session.get(TradeEntry, entry_id)
        assert entry.status == "closed_tp"

        exit_row = (
            await session.execute(select(TradeExit).where(TradeExit.trade_entry_id == entry_id))
        ).scalar_one()
        assert exit_row.exit_price == Decimal("110")
        expected_fees = settings.taker_fee * (Decimal("1000") + Decimal("1100"))
        expected_pnl = Decimal("1100") - Decimal("1000") - expected_fees
        assert exit_row.pnl_quote == expected_pnl

        equity_after = await get_latest_equity(session, settings)
        assert equity_after == equity_before + expected_pnl

        event_types = {
            row.event_type
            for row in (
                await session.execute(select(PositionEvent).where(PositionEvent.trade_entry_id == entry_id))
            ).scalars()
        }
        assert event_types == {"paper_open", "paper_exit"}

        portfolio = await build_portfolio_snapshot(session, settings, TEST_ASSET, now=exit_time)
        assert portfolio.equity_quote == equity_after


@pytest.mark.asyncio
async def test_update_open_positions_closes_on_real_candles():
    settings = get_settings()
    now = datetime.now(tz=UTC)
    technical_signal = _technical_signal(now)
    verdict = _approved_verdict(Decimal("1000"))

    async with get_session() as session:
        await upsert_asset(session, TEST_ASSET)
        decision_log_id = await _log_test_decision(session, now)
        entry = await open_position(
            session,
            settings,
            decision_log_id=decision_log_id,
            asset=TEST_ASSET,
            technical_signal=technical_signal,
            risk_verdict=verdict,
            entry_price=Decimal("100"),
            now=now,
        )
        entry_id = entry.id

        candle_that_hits_tp = Candle(
            asset=TEST_ASSET,
            timeframe="1h",
            open_time=now + timedelta(hours=1),
            close_time=now + timedelta(hours=2),
            open=Decimal("105"),
            high=Decimal("111"),
            low=Decimal("104"),
            close=Decimal("110"),
            volume=Decimal("1000"),
            quote_volume=Decimal("100000"),
        )
        await upsert_candles(session, [candle_that_hits_tp])
        await session.commit()

    async with get_session() as session:
        counts = await update_open_positions(session, settings, now + timedelta(hours=3))
        await session.commit()
    assert counts["checked"] >= 1

    async with get_session() as session:
        entry = await session.get(TradeEntry, entry_id)
        assert entry.status == "closed_tp"
        exit_row = (
            await session.execute(select(TradeExit).where(TradeExit.trade_entry_id == entry_id))
        ).scalar_one()
        assert exit_row.exit_price == Decimal("110")
        assert exit_row.exit_type == "closed_tp"
