from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, func, or_
from sqlmodel import Field, Session, SQLModel, col, select

from dtapp.garage.core.db import engine


class Consultant(SQLModel, table=True):
    __tablename__ = "consultant"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True, unique=True)  # stable key for upsert; stored lowercase+trimmed
    # Soft-delete: people leave AND rejoin; never hard-delete so FK refs still resolve.
    is_active: bool = Field(default=True)
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


def get_consultant(consultant_id: int) -> Optional["Consultant"]:
    with Session(engine) as session:
        return session.get(Consultant, consultant_id)


def get_consultant_by_email(email: str) -> Optional["Consultant"]:
    with Session(engine) as session:
        stmt = select(Consultant).where(Consultant.email == email.strip().lower())
        return session.exec(stmt).first()


def list_consultants(q: Optional[str] = None) -> list[Consultant]:
    """Active consultants (the assign picker) ordered by name asc; optionally filtered
    by name/email substring. Soft-deleted (departed) people are excluded — but stay
    fetchable via get_consultant(id) so existing FK references still display."""
    with Session(engine) as session:
        stmt = select(Consultant).where(Consultant.is_active == True)  # noqa: E712
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(or_(col(Consultant.name).ilike(pattern), col(Consultant.email).ilike(pattern)))
        stmt = stmt.order_by(Consultant.name)
        return list(session.exec(stmt).all())


def upsert_consultant(name: str, email: str) -> Consultant:
    """Upsert by email (the sheet's natural key). On match: update name + reactivate
    (is_active=True), so a returning person is reactivated with their name kept current.
    On no match: insert a new active row.

    NOTE: keyed by email, so a *changed* email reads as old-row (soft-deleted by the
    reconcile) + new-row added. Name changes are handled in place; true email-change
    continuity would need a stable person-id column in the sheet (not built).
    """
    email = email.strip().lower()
    name = name.strip()
    with Session(engine) as session:
        stmt = select(Consultant).where(Consultant.email == email)
        existing = session.exec(stmt).first()
        if existing:
            dirty = False
            if existing.name != name:
                existing.name = name
                dirty = True
            if not existing.is_active:
                existing.is_active = True
                dirty = True
            if dirty:
                session.add(existing)
                session.commit()
            session.refresh(existing)
            return existing
        row = Consultant(name=name, email=email)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def deactivate_consultant(email: Optional[str] = None, consultant_id: Optional[int] = None) -> bool:
    """Soft-delete a consultant by email or id (set is_active=False). Returns True if a row was updated."""
    with Session(engine) as session:
        if consultant_id is not None:
            row = session.get(Consultant, consultant_id)
        elif email is not None:
            row = session.exec(select(Consultant).where(Consultant.email == email.strip().lower())).first()
        else:
            raise ValueError("deactivate_consultant requires email or consultant_id")
        if row is None or not row.is_active:
            return False
        row.is_active = False
        session.add(row)
        session.commit()
        return True
