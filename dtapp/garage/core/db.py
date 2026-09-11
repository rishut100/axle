"""Shared SQLAlchemy engine for every garage module (kickstart + assets today). Lives in garage/core
so no single module owns the DB connection. Reads settings.garage_db_url (GARAGE_DB_* env)."""
from sqlmodel import create_engine

from dtapp.garage.core.config import settings

engine = create_engine(
    settings.garage_db_url,
    pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=1800,  # low-concurrency: API + 1 SQS consumer
)
