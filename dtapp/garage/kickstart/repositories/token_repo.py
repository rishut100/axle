from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, func, update
from sqlmodel import Field, Session, SQLModel, select

from dtapp.garage.core.db import engine


class IntakeToken(SQLModel, table=True):
    __tablename__ = "intake_token"

    token_hash: str = Field(primary_key=True)
    koid: str = Field(unique=True)  # one intake link per kickoff — tokens never expire, never resend
    email: Optional[str] = None
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    )
    used_at: Optional[datetime] = None


def save_token(token_hash, koid, email):
    with Session(engine) as session:
        session.add(IntakeToken(token_hash=token_hash, koid=koid, email=email))
        session.commit()


def get_token(token_hash) -> Optional[IntakeToken]:
    """Return token row by hash regardless of used state."""
    with Session(engine) as session:
        return session.get(IntakeToken, token_hash)


def get_valid_token(token_hash) -> Optional[IntakeToken]:
    """Return token only if unused (links never expire)."""
    with Session(engine) as session:
        stmt = select(IntakeToken).where(
            IntakeToken.token_hash == token_hash,
            IntakeToken.used_at == None,  # noqa: E711 — SQLModel requires == None for IS NULL
        )
        return session.exec(stmt).first()


def get_latest_token(koid) -> Optional[IntakeToken]:
    """Return the intake token for a koid, or None. There's at most one (koid is unique); the
    order_by is belt-and-suspenders. Drives the detail-page intake state (valid / submitted)."""
    with Session(engine) as session:
        stmt = (
            select(IntakeToken)
            .where(IntakeToken.koid == koid)
            .order_by(IntakeToken.created_at.desc())
        )
        return session.exec(stmt).first()


def consume_token(token_hash) -> Optional[str]:
    """Atomic CAS: mark token used only if currently unused; return koid if won, else None.
    This single-use guard IS the 'one submission per form' guarantee (koid is unique → one token)."""
    stmt = (
        update(IntakeToken)
        .where(
            IntakeToken.token_hash == token_hash,
            IntakeToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(timezone.utc))
        .returning(IntakeToken.koid)
    )
    with Session(engine) as session:
        result = session.execute(stmt)
        session.commit()
        row = result.fetchone()
        return row[0] if row else None
