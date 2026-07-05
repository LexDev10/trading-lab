"""Rearme manual tras un halt (sección 9.2: el drawdown killswitch "requiere
rearme manual por CLI/endpoint" — nunca automático).

Uso (con el stack levantado):
    docker compose exec app uv run python -m scripts.rearm
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from db.models import SystemState
from db.session import get_session
from notifications.telegram import send_message


async def rearm() -> None:
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        stmt = (
            pg_insert(SystemState)
            .values(id=1, state="running", halted_at=None, halted_reason=None, rearmed_at=now, updated_at=now)
            .on_conflict_do_update(
                index_elements=[SystemState.id],
                set_={"state": "running", "rearmed_at": now, "updated_at": now},
            )
        )
        await session.execute(stmt)
        await session.commit()
    print("Sistema rearmado. state=running.")
    await send_message(get_settings(), "✅ <b>Sistema rearmado</b>. state=running.")


if __name__ == "__main__":
    asyncio.run(rearm())
