from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, col, select

from dtapp.garage.core.db import engine


class Outbox(SQLModel, table=True):
    """Durable enqueue-intent. A row is inserted in the SAME transaction as the status change it
    corresponds to (so the enqueue can never be lost), then published to SQS and marked 'sent'.
    payload IS the exact SQS MessageBody the consumer expects — the relay sends it verbatim."""
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_status_created_at", "status", "created_at"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    koid: str = Field(sa_column=Column(String, nullable=False))
    type: str = Field(sa_column=Column(String, nullable=False))          # register | provision | assign
    payload: dict = Field(sa_column=Column(JSONB, nullable=False))       # exact SQS body
    status: str = Field(
        default="pending",
        sa_column=Column(String, nullable=False, server_default=text("'pending'")),
    )
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    sent_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )


def add_to_session(session, koid: str, type_: str, payload: dict) -> None:
    """Insert a pending outbox row into the caller's OPEN session so the enqueue-intent commits
    atomically with the status change. No commit here — the caller owns the transaction."""
    session.add(Outbox(koid=koid, type=type_, payload=payload, status="pending"))


def list_pending_for_koid(koid: str) -> list[Outbox]:
    """Fast path: the just-committed pending rows for one koid (published immediately post-commit)."""
    with Session(engine) as session:
        stmt = select(Outbox).where(Outbox.koid == koid, Outbox.status == "pending")
        return list(session.exec(stmt).all())


def list_pending(older_than_seconds: int, limit: int, types: list[str] | None = None) -> list[Outbox]:
    """Relay backstop: pending rows older than the grace window (a fast-path miss or a crash between
    commit and publish), oldest first, capped at limit.

    `types`: since ENG-90754 (dtapp/access) this table is shared across modules that each publish to
    their OWN SQS queue by `type` (register|provision|assign for kickstart; grant|revoke for access) —
    pass this caller's own type list so its relay sweep can never pick up another module's stuck row
    and publish it to the wrong queue. `None` (the pre-ENG-90754 default) means "no filter" for callers
    that haven't been updated; every relay caller SHOULD pass its own types."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    with Session(engine) as session:
        stmt = (
            select(Outbox)
            .where(Outbox.status == "pending", col(Outbox.created_at) < cutoff)
        )
        if types:
            stmt = stmt.where(col(Outbox.type).in_(types))
        stmt = stmt.order_by(col(Outbox.created_at).asc()).limit(limit)
        return list(session.exec(stmt).all())


def mark_sent(outbox_id: int) -> None:
    """Mark a row published (status='sent', sent_at=now) so the relay never re-publishes it."""
    with Session(engine) as session:
        row = session.get(Outbox, outbox_id)
        if row is None:
            return
        row.status = "sent"
        row.sent_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
