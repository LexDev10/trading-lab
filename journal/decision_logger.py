"""Persiste `DecisionRecord` en `decision_logs` — SIEMPRE, aceptado o
rechazado (principio de diseño 5)."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.schemas.decision import DecisionRecord
from db.models import DecisionLog


async def log_decision(session: AsyncSession, record: DecisionRecord) -> int:
    row = DecisionLog(
        ts=record.ts,
        mode=record.mode,
        trigger=record.trigger.value,
        asset=record.asset,
        git_sha=record.git_sha,
        scanner_jsonb=record.scanner,
        technical_jsonb=record.technical,
        fundamental_jsonb=record.fundamental,
        decision_jsonb=record.decision,
        risk_verdict_jsonb=record.risk_verdict,
        final_action=record.final_action.value,
        rejection_reasons=[r.value for r in record.rejection_reasons],
        expected_tp=record.expected_tp,
        expected_sl=record.expected_sl,
        horizon_class=record.horizon_class.value if record.horizon_class else None,
    )
    session.add(row)
    await session.flush()
    return row.id
