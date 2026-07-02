"""Halt manual inmediato (sección 9.2 / 15: "Endpoint/CLI de halt manual
inmediato"). Pone el sistema en modo `halt`: el risk engine deja de
aprobar nuevas entradas (check `system_not_halted`) hasta rearme manual
explícito vía `scripts/rearm.py`.

Uso (con el stack levantado):
    docker compose exec app uv run python -m scripts.halt "motivo del halt"
"""

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from db.models import SystemState
from db.session import get_session


async def halt(reason: str) -> None:
    now = datetime.now(tz=UTC)
    async with get_session() as session:
        stmt = (
            pg_insert(SystemState)
            .values(id=1, state="halt", halted_at=now, halted_reason=reason, rearmed_at=None, updated_at=now)
            .on_conflict_do_update(
                index_elements=[SystemState.id],
                set_={"state": "halt", "halted_at": now, "halted_reason": reason, "updated_at": now},
            )
        )
        await session.execute(stmt)
        await session.commit()
    print(f"Sistema en HALT. Motivo: {reason!r}. Rearme manual con: python -m scripts.rearm")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Halt manual inmediato del sistema")
    parser.add_argument("reason", help="Motivo del halt (se persiste en system_state.halted_reason)")
    args = parser.parse_args()
    asyncio.run(halt(args.reason))
