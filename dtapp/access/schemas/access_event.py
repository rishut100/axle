"""Normalized event DTO — the translation-layer boundary between a trigger source's actual payload
shape (Keka's webhook fields, or a manual API caller's body) and the rest of the module. Nothing
downstream of `event_service.ingest_event` should know Keka's field names."""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, field_validator


class NormalizedAccessEvent(BaseModel):
    external_id: str                       # source system's id for this person — idempotency key
    event_type: Literal["onboard", "offboard"]
    employee_email: EmailStr
    employee_name: str
    team: str                              # matrix lookup key — must match access_tool_matrix.team
    role: Optional[str] = None             # secondary matrix key (role-specific overrides)
    manager_email: Optional[EmailStr] = None
    effective_date: date                   # start date (onboard) or last working day (offboard)
    source: Literal["keka_webhook", "manual_api"] = "manual_api"
    raw: dict = {}                         # original payload, stashed for audit/debug

    @field_validator("employee_email", "manager_email", mode="before")
    @classmethod
    def _lowercase_email(cls, v):
        return v.strip().lower() if isinstance(v, str) else v


def from_keka_payload(payload: dict) -> NormalizedAccessEvent:
    """Adapter: Keka webhook body -> NormalizedAccessEvent. Isolated here so a Keka field-name change
    (or a future second HRIS) only touches this function, not the matrix/repo/steps layers.

    NOTE: field names below are best-guess placeholders pending a real Keka webhook payload sample —
    confirm against Keka's actual "Employee Onboarded"/"Employee Exit" webhook docs before go-live and
    adjust this mapping. Keeping the mapping in one small function makes that a one-file change.
    """
    event_type = "offboard" if (payload.get("eventType") or "").lower() in ("exit", "offboarded") else "onboard"
    return NormalizedAccessEvent(
        external_id=str(payload["employeeId"]),
        event_type=event_type,
        employee_email=payload["email"],
        employee_name=payload.get("displayName") or payload.get("name") or "",
        team=payload.get("department") or payload.get("team") or "",
        role=payload.get("designation") or payload.get("role"),
        manager_email=payload.get("managerEmail"),
        effective_date=payload.get("effectiveDate") or payload.get("dateOfJoining") or payload.get("lastWorkingDay"),
        source="keka_webhook",
        raw=payload,
    )
