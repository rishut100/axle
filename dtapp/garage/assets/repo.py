from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, func
from sqlmodel import Field, Session, SQLModel, select

from dtapp.garage.core.db import engine


class Asset(SQLModel, table=True):
    __tablename__ = "asset"

    id: Optional[int] = Field(default=None, primary_key=True)
    purpose: str = Field(index=True)            # e.g. "order_form", "attachment"
    reference_id: str = Field(index=True)       # the owning koid (TEXT) — generic
    s3_key: str = Field(index=True)             # unique-ish object key
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


def create_asset(**fields) -> Asset:
    with Session(engine) as session:
        row = Asset(**fields)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def get_asset(asset_id: int) -> Optional[Asset]:
    with Session(engine) as session:
        return session.get(Asset, asset_id)


def list_assets(reference_id: str, purpose: Optional[str] = None) -> list[Asset]:
    with Session(engine) as session:
        stmt = select(Asset).where(Asset.reference_id == reference_id)
        if purpose is not None:
            stmt = stmt.where(Asset.purpose == purpose)
        stmt = stmt.order_by(Asset.created_at)
        return list(session.exec(stmt).all())
