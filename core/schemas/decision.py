"""Contrato agregado que se persiste en `decision_logs`, siempre — aceptado
o rechazado. Ver sección 7.5 y 16 de ESPECIFICACION_SISTEMA_TRADING.md."""

from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel

from core.enums import FinalAction, HorizonClass, RejectionReason, Trigger

Mode = Literal["technical_only", "technical_plus_fundamental", "full"]


class DecisionRecord(BaseModel):
    ts: AwareDatetime
    mode: Mode
    trigger: Trigger
    asset: str
    git_sha: str

    scanner: dict[str, Any]
    technical: dict[str, Any] | None = None
    fundamental: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    risk_verdict: dict[str, Any] | None = None

    final_action: FinalAction
    rejection_reasons: list[RejectionReason] = []
    expected_tp: Decimal | None = None
    expected_sl: Decimal | None = None
    horizon_class: HorizonClass | None = None
    ollama_model: str | None = None
