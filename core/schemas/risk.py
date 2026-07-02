"""Contrato del veredicto del risk engine. Ver sección 7.4 y 9 de
ESPECIFICACION_SISTEMA_TRADING.md.

`checks` siempre contiene el resultado de TODOS los checks, se aprueben o
no (el engine no cortocircuita)."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from core.enums import RejectionReason


class RiskVerdict(BaseModel):
    approved: bool
    rejection_reasons: list[RejectionReason]
    checks: dict[str, bool]
    size_quote: Decimal | None
    rr_net_of_fees: Decimal | None
    portfolio_snapshot: dict[str, Any]
