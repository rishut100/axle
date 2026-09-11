"""AccessEvent — one row per (person, onboard|offboard action), with a JSONB per-tool checklist.
Mirrors dtapp/garage/kickstart/repositories/kickoff_repo.py: same reassign-not-mutate JSONB discipline
(SQLAlchemy's dirty-check misses an in-place dict mutation), same koid-style TEXT sequence primary key,
same status-machine-guarded status setter. See the plan's decision #3 for why one row per EVENT (not
one row per event+tool) — the offboarding-verify scan and the overall "is this done" rollup both want
a single-row JSONB read, not a join+groupby.
"""
import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Date, Sequence, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, col, select

from dtapp.garage.core.db import engine
from dtapp.garage.core.errors import ConflictError
from dtapp.access.enums import AccessEventStatus
from dtapp.access.status_machine import is_legal_transition
# Reused directly, not reimplemented — the Outbox table's `type` column already exists precisely to
# discriminate message kinds (decision #4: shared outbox table, dedicated queue). `koid` in that
# model is really just "the row-key string"; an AccessEvent's aeid fits it with zero changes needed.
from dtapp.garage.kickstart.repositories import outbox_repo

logger = logging.getLogger(__name__)

aeid_seq = Sequence("aeid_seq", start=1000, metadata=SQLModel.metadata)


class AccessEvent(SQLModel, table=True):
    __tablename__ = "access_event"

    aeid: Optional[str] = Field(
        default=None,
        sa_column=Column(String, aeid_seq, primary_key=True,
                         server_default=text("'AE-' || nextval('aeid_seq')")),
    )
    external_id: str = Field(sa_column=Column(String, nullable=False, unique=True))  # source-system id; idempotent re-ingest
    event_type: str = Field(sa_column=Column(String, nullable=False))                # "onboard" | "offboard"
    employee_email: str = Field(sa_column=Column(String, nullable=False, index=True))
    employee_name: str = ""
    team: str = ""
    role: Optional[str] = None
    manager_email: Optional[str] = None
    effective_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    status: AccessEventStatus = Field(
        default=AccessEventStatus.PENDING,
        sa_column=Column(String, nullable=False, server_default=AccessEventStatus.PENDING.value),
    )
    # {"github": {"method":"api","client_name":"github","status":"granted","done_at":...,"error":null},
    #  "clay": {"method":"manual","status":"ticket_open","linear_issue_id":"...","owner_email":...}, ...}
    checklist: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    references: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    task_errors: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    completed_steps: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    # Offboard-only: when set and in the past, the revoke-verify cron picks this row up (decision #7/#8).
    revoke_verify_due_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    revoke_verified_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    # Onboard-only mirror, added after real-world testing surfaced the missing grant-side check.
    grant_verify_due_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    grant_verified_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    created_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


def init_schema():
    SQLModel.metadata.create_all(engine, tables=[AccessEvent.__table__])
    with engine.begin() as conn:
        # Idempotent column adds for tables created before grant-side verification existed
        # (create_all won't ALTER an existing table — same pattern as kickoff_repo.init_schema).
        conn.execute(text("ALTER TABLE access_event ADD COLUMN IF NOT EXISTS grant_verify_due_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE access_event ADD COLUMN IF NOT EXISTS grant_verified_at TIMESTAMPTZ"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_access_event_status ON access_event (status)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_access_event_verify_due ON access_event (revoke_verify_due_at) "
            "WHERE revoke_verify_due_at IS NOT NULL AND revoke_verified_at IS NULL"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_access_event_grant_verify_due ON access_event (grant_verify_due_at) "
            "WHERE grant_verify_due_at IS NOT NULL AND grant_verified_at IS NULL"))


def _mutate(aeid, fn):
    """Load the AccessEvent, apply fn(event) in place, commit. No-op if the row isn't found. Same
    collapse-the-boilerplate helper as kickoff_repo._mutate."""
    with Session(engine) as session:
        event = session.get(AccessEvent, aeid)
        if event is None:
            return
        fn(event)
        session.add(event)
        session.commit()


def get_event(aeid) -> Optional[AccessEvent]:
    with Session(engine) as session:
        return session.get(AccessEvent, aeid)


def get_by_external_id(external_id: str) -> Optional[AccessEvent]:
    with Session(engine) as session:
        return session.exec(select(AccessEvent).where(AccessEvent.external_id == external_id)).first()


def create_event_atomic(*, external_id, event_type, employee_email, employee_name, team, role,
                        manager_email, effective_date, checklist: dict, outbox_type: str,
                        publish: bool = True) -> AccessEvent:
    """Insert a new AccessEvent AND (if publish=True) its Outbox enqueue-intent in ONE transaction
    (mirrors kickoff_repo.begin_provisioning_atomic) — so the row can never commit without a matching
    queue intent, and a crash between commit and SQS-publish just leaves a pending Outbox row for the
    relay sweep to pick up (flush_outbox), never a silently-lost event.

    publish=False skips the Outbox insert entirely — for local test/coverage scripts that call
    run_grant_steps/run_revoke_steps directly in-process. THIS MATTERS: publish=True (the default)
    queues a REAL SQS message regardless of how ingest_event was invoked, and if a real-credentialed
    server/consumer happens to be running at the same time (e.g. a persistent local dev server), it
    will ALSO process that message for real, independent of whatever the calling script does in-
    process — confirmed the hard way when a "safe" coverage-check test silently created ~40 real
    Linear tickets because a background server was consuming the same queue. Any test/dry-run
    ingestion that isn't specifically exercising the real webhook-to-SQS-to-consumer path should pass
    publish=False.

    Raises on a duplicate external_id (idempotent re-ingest is the caller's job: check
    get_by_external_id first — the unique constraint is the last-resort backstop, not the primary check).
    """
    with Session(engine) as session:
        # Compute aeid explicitly rather than relying on the column's server_default: SQLAlchemy
        # treats a Sequence bound directly to a Column (aeid_seq above) as a client-side "fetch
        # nextval() before INSERT" default, which takes priority over — and bypasses — the
        # 'AE-' || nextval(...) server_default text expression. Confirmed by hand: the server_default
        # is correctly present in the DDL (\d access_event shows it), but a real insert came back as
        # bare "1000" instead of "AE-1000". Fetching nextval() ourselves sidesteps the ambiguity.
        seq_val = session.execute(text("SELECT nextval('aeid_seq')")).scalar()
        aeid = f"AE-{seq_val}"
        event = AccessEvent(
            aeid=aeid, external_id=external_id, event_type=event_type, employee_email=employee_email,
            employee_name=employee_name, team=team, role=role, manager_email=manager_email,
            effective_date=effective_date, checklist=checklist,
        )
        session.add(event)
        session.flush()
        if publish:
            outbox_repo.add_to_session(session, event.aeid, outbox_type, {"aeid": event.aeid, "type": outbox_type})
        session.commit()
        session.refresh(event)
        return event


def _apply_status(event: AccessEvent, target: AccessEventStatus):
    if not is_legal_transition(AccessEventStatus(event.status), target):
        raise ConflictError(f"illegal status transition {event.status} -> {target} for {event.aeid}")
    event.status = target


def set_status(aeid, target: AccessEventStatus):
    _mutate(aeid, lambda e: _apply_status(e, target))


def set_checklist_item(aeid, tool: str, **fields):
    """Merge fields into checklist[tool] (status, done_at, error, linear_issue_id, scim_user_id, ...).
    Reassigns checklist so SQLAlchemy detects the JSONB mutation (see kickoff_repo's add_reference)."""
    def _set(e):
        item = {**(e.checklist.get(tool) or {}), **fields}
        e.checklist = {**e.checklist, tool: item}
    _mutate(aeid, _set)


def mark_step_done(aeid, key: str):
    def _set(e):
        e.completed_steps = {**(e.completed_steps or {}), key: True}
    _mutate(aeid, _set)


def record_task_error(aeid, stage: str, key: str, message: str):
    def _set(e):
        e.task_errors = {**(e.task_errors or {}), f"{stage}:{key}": message}
    _mutate(aeid, _set)


def merge_references(aeid, refs: dict):
    def _set(e):
        e.references = {**(e.references or {}), **(refs or {})}
    _mutate(aeid, _set)


def set_revoke_verify_due(aeid, due_at: datetime):
    _mutate(aeid, lambda e: setattr(e, "revoke_verify_due_at", due_at))


def list_due_for_verify(now: datetime, limit: int = 100) -> list[AccessEvent]:
    with Session(engine) as session:
        stmt = (
            select(AccessEvent)
            .where(
                col(AccessEvent.revoke_verify_due_at).is_not(None),
                col(AccessEvent.revoke_verify_due_at) <= now,
                col(AccessEvent.revoke_verified_at).is_(None),
            )
            .order_by(col(AccessEvent.revoke_verify_due_at).asc())
            .limit(limit)
        )
        return list(session.exec(stmt).all())


def record_drift(aeid, tool: str, observed_status: str):
    def _set(e):
        item = {**(e.checklist.get(tool) or {}), "drift_observed_status": observed_status,
                "drift_detected_at": datetime.now(timezone.utc).isoformat()}
        e.checklist = {**e.checklist, tool: item}
    _mutate(aeid, _set)


def mark_verified(aeid):
    _mutate(aeid, lambda e: setattr(e, "revoke_verified_at", datetime.now(timezone.utc)))


def set_grant_verify_due(aeid, due_at: datetime):
    _mutate(aeid, lambda e: setattr(e, "grant_verify_due_at", due_at))


def list_due_for_grant_verify(now: datetime, limit: int = 100) -> list[AccessEvent]:
    with Session(engine) as session:
        stmt = (
            select(AccessEvent)
            .where(
                col(AccessEvent.grant_verify_due_at).is_not(None),
                col(AccessEvent.grant_verify_due_at) <= now,
                col(AccessEvent.grant_verified_at).is_(None),
            )
            .order_by(col(AccessEvent.grant_verify_due_at).asc())
            .limit(limit)
        )
        return list(session.exec(stmt).all())


def mark_grant_verified(aeid):
    _mutate(aeid, lambda e: setattr(e, "grant_verified_at", datetime.now(timezone.utc)))


def rollup_status(aeid) -> AccessEventStatus:
    """Compute DONE/PARTIAL_FAILED from the checklist — every entry resolved (granted/revoked/done) ->
    DONE; anything still pending/ticket_open/failed -> PARTIAL_FAILED. Caller (the queue consumer,
    after a grant/revoke pass, and the Slack-reaction handler, after a manual tool completes) persists
    the result via set_status."""
    event = get_event(aeid)
    if event is None:
        raise ValueError(f"no access_event {aeid}")
    terminal_ok = {"granted", "revoked", "done"}
    if all((item.get("status") in terminal_ok) for item in event.checklist.values()):
        return AccessEventStatus.DONE
    return AccessEventStatus.PARTIAL_FAILED
