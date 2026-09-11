"""Role -> tool -> owner matrix — the single source of truth this whole project exists to create,
replacing the tribal-knowledge Slack mentions + the ad hoc 'Tenant Status' Google Sheet. A DB table
(not a synced Sheet, not a code constant) so it's admin-editable without a deploy and validated at
write time — see the plan's decision #2 for the full reasoning.

SECURITY NOTE: `client_config` currently holds per-tool SCIM base_url/bearer-token pairs in plaintext
JSONB. Fine to ship with (mirrors no worse than kickstart's plaintext settings fields), but flagged as
a TODO to move to AWS Secrets Manager (referenced by key, not stored inline) once the matrix has more
than a handful of configured tools — a DB dump would otherwise leak every tool's admin credentials.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, select

from dtapp.garage.core.db import engine

logger = logging.getLogger(__name__)


class ToolMatrixEntry(SQLModel, table=True):
    __tablename__ = "access_tool_matrix"

    id: Optional[int] = Field(default=None, primary_key=True)
    team: str = Field(sa_column=Column(String, nullable=False, index=True))   # "*" = every team
    role: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))  # None = every role
    tool_name: str = Field(sa_column=Column(String, nullable=False))          # display name, e.g. "GitHub"
    method: str = Field(sa_column=Column(String, nullable=False))             # "api" | "manual"
    client_name: Optional[str] = None       # dispatch key into access_steps.common.CLIENT_REGISTRY
    client_config: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    owner_email: Optional[str] = None       # manual-tool ticket assignee / escalation contact
    owner_slack_id: Optional[str] = None
    is_active: bool = Field(default=True)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False, onupdate=func.now()),
    )


def init_schema():
    SQLModel.metadata.create_all(engine, tables=[ToolMatrixEntry.__table__])


def resolve_for(team: str, role: Optional[str]) -> list[ToolMatrixEntry]:
    """Matrix lookup for a (team, role): most-specific-wins per tool_name — an exact (team, role) row
    beats a (team, None) row, which beats a ("*", None) row. Returns one entry per distinct tool_name."""
    with Session(engine) as session:
        candidates = session.exec(
            select(ToolMatrixEntry).where(
                ToolMatrixEntry.is_active == True,  # noqa: E712
                ToolMatrixEntry.team.in_([team, "*"]),
            )
        ).all()

    def _specificity(e: ToolMatrixEntry) -> int:
        if e.team == team and e.role == role:
            return 3
        if e.team == team and e.role is None:
            return 2
        if e.team == "*" and e.role == role:
            return 1
        return 0

    best_by_tool: dict[str, ToolMatrixEntry] = {}
    for entry in candidates:
        if entry.role is not None and entry.role != role:
            continue  # a role-scoped row that doesn't match this event's role never applies
        current = best_by_tool.get(entry.tool_name)
        if current is None or _specificity(entry) > _specificity(current):
            best_by_tool[entry.tool_name] = entry
    return list(best_by_tool.values())


def upsert(entry_id: Optional[int], updated_by: str, **fields) -> ToolMatrixEntry:
    with Session(engine) as session:
        if entry_id:
            row = session.get(ToolMatrixEntry, entry_id)
            if row is None:
                raise ValueError(f"no access_tool_matrix row with id={entry_id}")
        else:
            row = ToolMatrixEntry()
            session.add(row)
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_by = updated_by
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return row


def list_all(team: Optional[str] = None) -> list[ToolMatrixEntry]:
    with Session(engine) as session:
        stmt = select(ToolMatrixEntry).where(ToolMatrixEntry.is_active == True)  # noqa: E712
        if team:
            stmt = stmt.where(ToolMatrixEntry.team == team)
        return list(session.exec(stmt.order_by(ToolMatrixEntry.team, ToolMatrixEntry.tool_name)).all())


def deactivate(entry_id: int, updated_by: str) -> None:
    upsert(entry_id, updated_by, is_active=False)
