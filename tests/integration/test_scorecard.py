"""`compute_weekly_scorecard` contra Postgres real (sección 12.3/16):
hit-rate y retorno firmado por `(stance, horizon)`, sobre velas y
clasificaciones sintéticas deterministas. Usa un activo ficticio
(`ZZTESTUSDT`) para no tocar ni depender de las velas reales del universo
ya ingeridas."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from core.schemas.market import Candle
from db.models import Candle as CandleRow
from db.models import ClassifierScorecard as ClassifierScorecardRow
from db.models import ItemClassification as ItemClassificationRow
from db.session import get_session
from services.data.persistence import upsert_candles
from services.fundamental.scorecard import compute_weekly_scorecard

pytestmark = pytest.mark.integration

TEST_ASSET = "ZZTESTUSDT"
TEST_MODEL = "zz_test_scorecard_model"
# Lunes de la semana "actual" (cuando correría el job) y viernes de la
# semana ANTERIOR (la que el job evalúa) — dentro de [last_week_start,
# this_week_start).
THIS_WEEK_MONDAY = datetime(2026, 1, 12, tzinfo=UTC)
JOB_NOW = THIS_WEEK_MONDAY.replace(hour=0, minute=5)
CLASSIFIED_AT = THIS_WEEK_MONDAY - timedelta(days=3)


def _candle(open_time: datetime, close: str) -> Candle:
    return Candle(
        asset=TEST_ASSET,
        timeframe="1h",
        open_time=open_time,
        close_time=open_time + timedelta(hours=1),
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        quote_volume=Decimal("1"),
    )


def _classification(item_id: int, stance: str) -> ItemClassificationRow:
    return ItemClassificationRow(
        item_id=item_id,
        item_kind="news",
        model_name=TEST_MODEL,
        model_version="v1",
        classified_at=CLASSIFIED_AT,
        stance=stance,
        event_types=[],
        veto=False,
        summary="test",
        output_jsonb={},
        asset_tags=["ZZTEST"],
    )


@pytest.fixture(autouse=True)
async def _cleanup():
    await _delete_test_rows()
    yield
    await _delete_test_rows()


async def _delete_test_rows() -> None:
    async with get_session() as session:
        await session.execute(delete(ClassifierScorecardRow).where(ClassifierScorecardRow.stance.in_(["bullish_strong", "bearish_strong"])))
        await session.execute(delete(ItemClassificationRow).where(ItemClassificationRow.model_name == TEST_MODEL))
        await session.execute(delete(CandleRow).where(CandleRow.asset == TEST_ASSET))
        await session.commit()


async def test_compute_weekly_scorecard_hit_rate_and_signed_return():
    # Precios: 100 -> +10% a las 4h -> -10% a las 24h (vs. inicio) -> +5% a las 72h.
    candles = [
        _candle(CLASSIFIED_AT, "100"),
        _candle(CLASSIFIED_AT + timedelta(hours=4), "110"),
        _candle(CLASSIFIED_AT + timedelta(hours=24), "90"),
        _candle(CLASSIFIED_AT + timedelta(hours=72), "105"),
    ]
    async with get_session() as session:
        await upsert_candles(session, candles)
        session.add(_classification(1, "bullish_strong"))
        session.add(_classification(2, "bearish_strong"))
        session.add(_classification(3, "neutral"))  # no direccional -> excluido
        await session.commit()

    async with get_session() as session:
        counts = await compute_weekly_scorecard(session, JOB_NOW)
        await session.commit()

    assert counts["classifications_considered"] == 3
    assert counts["scorecard_rows"] == 6  # 2 stances direccionales x 3 horizontes

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ClassifierScorecardRow).where(ClassifierScorecardRow.week == (THIS_WEEK_MONDAY - timedelta(days=7)).date())
                )
            ).scalars()
        )
    by_key = {(r.stance, r.horizon): r for r in rows}
    assert set(by_key) == {
        ("bullish_strong", "4h"), ("bullish_strong", "24h"), ("bullish_strong", "72h"),
        ("bearish_strong", "4h"), ("bearish_strong", "24h"), ("bearish_strong", "72h"),
    }

    bullish_4h = by_key[("bullish_strong", "4h")]
    assert bullish_4h.n == 1
    assert bullish_4h.hit_rate == Decimal("1.0000")
    assert bullish_4h.avg_fwd_return == pytest.approx(Decimal("0.1"), abs=Decimal("0.0001"))

    bullish_24h = by_key[("bullish_strong", "24h")]
    assert bullish_24h.hit_rate == Decimal("0.0000")
    assert bullish_24h.avg_fwd_return == pytest.approx(Decimal("-0.1"), abs=Decimal("0.0001"))

    bearish_4h = by_key[("bearish_strong", "4h")]
    assert bearish_4h.hit_rate == Decimal("0.0000")
    assert bearish_4h.avg_fwd_return == pytest.approx(Decimal("-0.1"), abs=Decimal("0.0001"))

    bearish_24h = by_key[("bearish_strong", "24h")]
    assert bearish_24h.hit_rate == Decimal("1.0000")
    assert bearish_24h.avg_fwd_return == pytest.approx(Decimal("0.1"), abs=Decimal("0.0001"))


async def test_compute_weekly_scorecard_discards_horizon_without_enough_future_data():
    """FIX (2026-07-07, bug #14 CODE_REVIEW_2026-07-07.md, punto 1): sin
    vela real a la distancia del horizonte, el punto se descarta — antes
    se usaba la última vela disponible como aproximación, falseando un
    "retorno a 72h" medido en realidad sobre mucho menos tiempo."""
    # Solo hay velas hasta +4h: los horizontes 24h/72h no tienen dato real.
    candles = [
        _candle(CLASSIFIED_AT, "100"),
        _candle(CLASSIFIED_AT + timedelta(hours=4), "110"),
    ]
    async with get_session() as session:
        await upsert_candles(session, candles)
        session.add(_classification(10, "bullish_strong"))
        await session.commit()

    async with get_session() as session:
        counts = await compute_weekly_scorecard(session, JOB_NOW)
        await session.commit()

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ClassifierScorecardRow).where(
                        ClassifierScorecardRow.week == (THIS_WEEK_MONDAY - timedelta(days=7)).date()
                    )
                )
            ).scalars()
        )
    horizons_present = {r.horizon for r in rows if r.stance == "bullish_strong"}
    assert horizons_present == {"4h"}
    assert counts["scorecard_rows"] == 1


async def test_compute_weekly_scorecard_counts_item_for_every_tagged_asset():
    """FIX (2026-07-07, bug #14, punto 3): un item con varios `asset_tags`
    cuenta para CADA activo etiquetado, no solo `asset_tags[0]`."""
    other_asset = "ZZOTHERUSDT"
    candles_a = [_candle(CLASSIFIED_AT, "100"), _candle(CLASSIFIED_AT + timedelta(hours=4), "110")]
    candles_b = [
        Candle(
            asset=other_asset, timeframe="1h", open_time=CLASSIFIED_AT, close_time=CLASSIFIED_AT + timedelta(hours=1),
            open=Decimal("50"), high=Decimal("50"), low=Decimal("50"), close=Decimal("50"),
            volume=Decimal("1"), quote_volume=Decimal("1"),
        ),
        Candle(
            asset=other_asset, timeframe="1h", open_time=CLASSIFIED_AT + timedelta(hours=4),
            close_time=CLASSIFIED_AT + timedelta(hours=5),
            open=Decimal("55"), high=Decimal("55"), low=Decimal("55"), close=Decimal("55"),
            volume=Decimal("1"), quote_volume=Decimal("1"),
        ),
    ]
    async with get_session() as session:
        await upsert_candles(session, candles_a)
        await upsert_candles(session, candles_b)
        item = _classification(20, "bullish_strong")
        item.asset_tags = ["ZZTEST", "ZZOTHER"]
        session.add(item)
        await session.commit()

    async with get_session() as session:
        await compute_weekly_scorecard(session, JOB_NOW)
        await session.commit()

    async with get_session() as session:
        row = (
            await session.execute(
                select(ClassifierScorecardRow).where(
                    ClassifierScorecardRow.week == (THIS_WEEK_MONDAY - timedelta(days=7)).date(),
                    ClassifierScorecardRow.stance == "bullish_strong",
                    ClassifierScorecardRow.horizon == "4h",
                )
            )
        ).scalar_one()
    # Ambos activos suben +10% -> 2 puntos contados (no solo el primero).
    assert row.n == 2

    async with get_session() as session:
        await session.execute(delete(CandleRow).where(CandleRow.asset == other_asset))
        await session.commit()


async def test_compute_weekly_scorecard_is_idempotent_on_rerun():
    """FIX (2026-07-07, bug #14, punto 2): re-ejecutar el job para la
    MISMA semana hace upsert, no duplica filas."""
    candles = [_candle(CLASSIFIED_AT, "100"), _candle(CLASSIFIED_AT + timedelta(hours=4), "110")]
    async with get_session() as session:
        await upsert_candles(session, candles)
        session.add(_classification(30, "bullish_strong"))
        await session.commit()

    async with get_session() as session:
        await compute_weekly_scorecard(session, JOB_NOW)
        await session.commit()
    async with get_session() as session:
        await compute_weekly_scorecard(session, JOB_NOW)
        await session.commit()

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ClassifierScorecardRow).where(
                        ClassifierScorecardRow.week == (THIS_WEEK_MONDAY - timedelta(days=7)).date(),
                        ClassifierScorecardRow.stance == "bullish_strong",
                        ClassifierScorecardRow.horizon == "4h",
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].n == 1
