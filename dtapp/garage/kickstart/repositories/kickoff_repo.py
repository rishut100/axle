import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Sequence, String, Text, func, or_, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, col, select

from dtapp.garage.core.db import engine
from dtapp.garage.core.errors import ConflictError
from dtapp.garage.kickstart.enums import KickoffStatus
from dtapp.garage.kickstart.status_machine import is_legal_transition
from dtapp.garage.kickstart.schemas.kickoff import flatten_groups
from dtapp.garage.kickstart.repositories import outbox_repo

logger = logging.getLogger(__name__)

# Declared once on the metadata so create_all issues CREATE SEQUENCE exactly once.
koid_seq = Sequence("koid_seq", start=1000, metadata=SQLModel.metadata)


class Kickoff(SQLModel, table=True):
    __tablename__ = "kickoff"

    koid: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, koid_seq, primary_key=True,
                         server_default=text("'KO-' || nextval('koid_seq')")),
    )
    registered_by: Optional[str] = None
    solution_consultant_id: Optional[int] = Field(default=None, foreign_key="consultant.id")
    analyst_id: Optional[int] = Field(default=None, foreign_key="consultant.id")
    pod_lead_id: Optional[int] = Field(default=None, foreign_key="consultant.id")
    tenant_id: Optional[str] = None
    # Kickstart Agent Slack channel: {"name": <str>, "channel_id": <str>}
    slack_channel_config: dict = Field(
        default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    )
    kickoff_date: Optional[date] = None  # booked kickoff date; denormalized from data for list filter/sort
    status: KickoffStatus = Field(
        default=KickoffStatus.AWAITING_CUSTOMER_RESPONSE,
        sa_column=Column(String, nullable=False,
                         server_default=KickoffStatus.AWAITING_CUSTOMER_RESPONSE.value),
    )
    data: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    status_log: list = Field(default=[], sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")))
    # Per-(kickoff, day) idempotency ledger, e.g. {"intake": "YYYY-MM-DD", "intake_email_seq": N}. Reassign, never mutate.
    reminders: dict = Field(default={}, sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    provisioning_done_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # Draft (autosaved, unsubmitted) register form. True = partial, no side-effects fired, excluded
    # from the ops list/detail; submit flips it False and runs the all-or-nothing register.
    is_draft: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    # Soft-delete flag; reads filter is_active=true (a real kickoff is never hard-deleted; drafts are).
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    created_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    # When the draft was submitted (is_draft → false) = the real "registered at". NULL while a draft.
    # created_at = when the draft was first started; updated_at = last autosave.
    submitted_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )


def init_schema():
    # Import the models that moved to sibling repos so their tables are registered on
    # SQLModel.metadata before create_all (else create_all would skip intake_token/consultant/asset).
    from dtapp.garage.kickstart.repositories import token_repo, consultant_repo, outbox_repo  # noqa: F401
    from dtapp.garage.assets import repo as asset_repo  # noqa: F401
    SQLModel.metadata.create_all(engine)
    # Idempotent column adds for existing dev tables (create_all won't ALTER an existing table).
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS analyst_id INTEGER"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS pod_lead_id INTEGER"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS is_draft BOOLEAN NOT NULL DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS kickoff_date DATE"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"))
        # Backfill kickoff_date from the stored blob for rows registered before the column existed.
        conn.execute(text(
            "UPDATE kickoff SET kickoff_date = (data->'deal_details'->>'kickoff_date')::date "
            "WHERE kickoff_date IS NULL AND (data->'deal_details'->>'kickoff_date') IS NOT NULL"))
        # Drop denormalized columns now sourced from the data blob (idempotent + fresh-db safe).
        for _col in ("company_name", "company_website_url", "poc_tenant_id", "channel_of_communication"):
            conn.execute(text(f"ALTER TABLE kickoff DROP COLUMN IF EXISTS {_col}"))
        # Indexes for the ops-list filters/sorts (create_all won't add these to an existing table).
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_status ON kickoff (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_registered_by ON kickoff (registered_by)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_created_at ON kickoff (created_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_kickoff_date ON kickoff (kickoff_date)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_is_draft ON kickoff (is_draft)"))
        # Drop ix_kickoff_is_active: a btree on a ~100%-true boolean is never used, only costs writes.
        conn.execute(text("DROP INDEX IF EXISTS ix_kickoff_is_active"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_consultant ON kickoff (solution_consultant_id)"))
        conn.execute(text("ALTER TABLE kickoff ADD COLUMN IF NOT EXISTS reminders JSONB NOT NULL DEFAULT '{}'::jsonb"))
        # Reminder-scan indexes (partial — the scans filter on active + status).
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_intake_reminder ON kickoff (status) WHERE is_active AND NOT is_draft"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_kickoff_consultant_reminder ON kickoff (status, kickoff_date) WHERE is_active"))
    # Trigram GIN indexes for the ops-list search — company_name (blob) + tenant_id are matched with
    # ILIKE '%q%' (leading wildcard → a btree can't help). Best-effort in its own txn: needs the pg_trgm
    # extension; if the DB role can't create it, skip (search still works via seq scan at this low scale)
    # rather than fail boot.
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            # Index the SAME coalesce(grouped, top-level) expression the search uses (see
            # _company_name_expr), else the index can't back the query.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_kickoff_company_name_trgm ON kickoff USING gin "
                "(COALESCE(data->'company_details'->>'company_name', data->>'company_name') gin_trgm_ops)"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_kickoff_tenant_id_trgm ON kickoff USING gin "
                "(tenant_id gin_trgm_ops)"))
    except Exception as e:  # noqa: BLE001 — search works without it at this scale; never fail boot
        logger.warning("[kickstart] pg_trgm search indexes skipped: %s", e)


def _append_status(kickoff, status_value: str):
    """Reassign status_log so SQLAlchemy detects the JSONB mutation."""
    kickoff.status_log = [*(kickoff.status_log or []), {"status": status_value, "created_at": datetime.now(timezone.utc).isoformat()}]


def _mutate(koid, fn):
    """Load the Kickoff, apply fn(kickoff) in place, commit. No-op if the row isn't found.
    Collapses the get → mutate → add → commit boilerplate shared by the field setters below."""
    with Session(engine) as session:
        kickoff = session.get(Kickoff, koid)
        if kickoff is None:
            return
        fn(kickoff)
        session.add(kickoff)
        session.commit()


def get_kickoff(koid) -> Optional[Kickoff]:
    """A real (submitted) kickoff. Drafts are excluded — they aren't kickoffs yet (resume via get_draft),
    so the detail/assign/intake paths never treat a draft as a real kickoff."""
    with Session(engine) as session:
        stmt = select(Kickoff).where(
            Kickoff.koid == koid, col(Kickoff.is_draft).is_(False), col(Kickoff.is_active).is_(True))
        return session.exec(stmt).first()


def get_draft(koid) -> Optional[Kickoff]:
    """Load a draft (unsubmitted) kickoff for resume."""
    with Session(engine) as session:
        stmt = select(Kickoff).where(
            Kickoff.koid == koid, col(Kickoff.is_draft).is_(True), col(Kickoff.is_active).is_(True))
        return session.exec(stmt).first()


def list_drafts(created_by) -> list[Kickoff]:
    """Creator's own drafts (unsubmitted), newest first. Drafts are private to their author."""
    with Session(engine) as session:
        stmt = (
            select(Kickoff)
            .where(col(Kickoff.is_draft).is_(True), Kickoff.registered_by == created_by,
                   col(Kickoff.is_active).is_(True))
            .order_by(col(Kickoff.updated_at).desc())
        )
        return list(session.exec(stmt).all())


def create_draft(data: dict, principal: str) -> str:
    """POST — create a new draft kickoff. Partial data, NO side-effects, NO validation. Returns the koid."""
    with Session(engine) as session:
        row = Kickoff(
            registered_by=principal,
            data=data,
            is_draft=True,
            status=KickoffStatus.AWAITING_CUSTOMER_RESPONSE,
        )
        session.add(row)
        session.flush()  # mint koid
        koid = row.koid
        session.commit()
        return koid


def update_draft(koid: str, data: dict) -> bool:
    """PUT — full-replace an existing draft's form state (autosave). Entertained ONLY while is_draft;
    returns False if the koid isn't found or has already been submitted (no longer a draft)."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or not row.is_draft:
            return False
        row.data = data
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return True


def delete_draft(koid: str, owner: str) -> bool:
    """DELETE — discard a draft. ONLY if it's still a draft AND owned by `owner` (creator-private);
    a submitted kickoff is never deleted here. Returns True if deleted, else False."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or not row.is_draft or row.registered_by != owner:
            return False
        session.delete(row)
        session.commit()
        return True


def soft_delete_kickoff(koid: str) -> bool:
    """Soft-delete a real (submitted) kickoff: is_active → false. The row stays but is filtered out of
    every read. Record-only (no Linear/Slack/Monday cleanup). True if deactivated, False if not found
    or already inactive."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or not row.is_active:
            return False
        row.is_active = False
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()
        return True


def submit_draft_atomic(koid: str, data: dict, registered_by: str) -> Optional[str]:
    """Promote a draft to a real kickoff: fill the row from the full payload, flip is_draft → False,
    set status REGISTERING, commit. NO external work runs here — the slow register-ancillary sequence
    runs async in handle_register (the outbox intent is written in this txn; the caller publishes it via flush_outbox after this returns). Returns the koid, or
    None if koid isn't an existing draft."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or not row.is_draft:
            return None
        # registered_by → typed column + mirrored into the blob (async steps CC the AE); kickoff_date
        # is denormalized from the blob for list sort/filter.
        flat = flatten_groups(data)
        row.registered_by = registered_by
        _kd = flat.get("kickoff_date")
        row.kickoff_date = date.fromisoformat(_kd[:10]) if _kd else None  # ISO 'YYYY-MM-DD' → date column
        # slack_channel_config stays {} at register; the internal channel is created post-tenant (completion).
        # Merge over the draft blob, not overwrite: preserve refs/workflow metadata across promotion (gotcha #3).
        row.data = {**row.data, **data, "registered_by": registered_by,
                    "references": {**(row.data.get("references") or {}), **(data.get("references") or {})}}
        row.is_draft = False
        now = datetime.now(timezone.utc)
        row.submitted_at = now  # the real "registered at" (distinct from created_at = draft start)
        row.status = KickoffStatus.REGISTERING
        row.status_log = [{
            "status": KickoffStatus.REGISTERING.value,
            "created_at": now.isoformat(),
        }]
        outbox_repo.add_to_session(session, koid, "register", {"koid": koid, "type": "register"})
        session.add(row)
        session.commit()
        return koid


def _apply_status(kickoff, status: KickoffStatus):
    """In-session status transition (NO commit): same-status is a no-op; an illegal current → target
    raises ConflictError (status_machine); a legal move sets status + appends to status_log. Shared by
    set_status_explicit and the atomic multi-step methods."""
    if kickoff.status == status:
        return
    if not is_legal_transition(kickoff.status, status):
        raise ConflictError(
            f"Illegal kickoff status transition {kickoff.status.value} → {status.value} for {kickoff.koid}."
        )
    kickoff.status = status
    _append_status(kickoff, status.value)


def set_status_explicit(koid, status: KickoffStatus):
    """Explicitly set status and append the transition to status_log (no recompute). Guards the move
    against the legal-transition table (status_machine): an illegal current → target raises
    ConflictError. Same-status writes are a no-op (idempotent re-set)."""
    _mutate(koid, lambda k: _apply_status(k, status))


def mark_failed(koid):
    """Best-effort FAILED marker (called at dead-letter); swallows ConflictError to self-scope to the work-transient statuses."""
    try:
        _mutate(koid, lambda k: _apply_status(k, KickoffStatus.FAILED))
    except ConflictError:
        pass


def begin_provisioning_atomic(koid, details: dict) -> bool:
    """Intake → provisioning, atomically: merge intake details into data, transition to PROVISIONING,
    and record the provision enqueue-intent in the outbox — all in ONE transaction, so the status can
    never be committed without a matching queue intent. Returns False if koid isn't a real (submitted)
    kickoff (a draft or absent)."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or row.is_draft:
            return False
        row.data = {**row.data, **details}
        _apply_status(row, KickoffStatus.PROVISIONING)
        outbox_repo.add_to_session(session, koid, "provision", {"koid": koid})
        session.add(row)
        session.commit()
        return True


def assign_consultant_atomic(koid, consultant_id, analyst_id, pod_lead_id, removed, clear_keys) -> bool:
    """(Re)assign atomically (unit-of-work): set the three role FKs, clear the given completed_steps
    flags (so the post-assign work re-runs), stash the removed emails, transition to CONSULTANT_ASSIGNED,
    and record the assign enqueue-intent — all in ONE transaction. The outbox row is inserted
    UNCONDITIONALLY (even when the status is already CONSULTANT_ASSIGNED — a re-assign no-op) so a
    re-assign always re-drives the post-assign work. Returns False if koid isn't a real kickoff."""
    with Session(engine) as session:
        row = session.get(Kickoff, koid)
        if row is None or row.is_draft:
            return False
        row.solution_consultant_id = consultant_id
        row.analyst_id = analyst_id
        row.pod_lead_id = pod_lead_id
        done = {**(row.data.get("completed_steps") or {})}
        for key in clear_keys or []:
            done.pop(key, None)
        row.data = {**row.data, "completed_steps": done, "_assign_removed": list(removed or [])}
        _apply_status(row, KickoffStatus.CONSULTANT_ASSIGNED)
        outbox_repo.add_to_session(session, koid, "assign", {"koid": koid, "type": "assign"})
        session.add(row)
        session.commit()
        return True


def update_kickoff_data(koid, extra: dict):
    """Merge extra into kickoff.data (status is owned by set_status_explicit)."""
    _mutate(koid, lambda k: setattr(k, "data", {**k.data, **extra}))


def update_kickoff_date(koid, iso: str):
    """Edit the booked kickoff date. Writes the GROUPED blob (deal_details.kickoff_date — the key
    flatten_groups actually reads) AND the denormalized column (list sort/filter); updating only the
    top-level blob key is masked by the deal_details value on flatten, so the edit wouldn't show."""
    def _set(k):
        deal = {**(k.data.get("deal_details") or {}), "kickoff_date": iso}
        k.data = {**k.data, "deal_details": deal}
        k.kickoff_date = date.fromisoformat(iso[:10])
    _mutate(koid, _set)


def update_kickoff_fields(koid, new_data: dict, kickoff_date_iso: Optional[str]):
    """Persist an edited kickoff: replace the data blob (already merged + validated by the service)
    and keep the denormalized kickoff_date column in sync (list sort/filter reads the column)."""
    def _set(k):
        k.data = new_data
        if kickoff_date_iso:
            k.kickoff_date = date.fromisoformat(kickoff_date_iso[:10])
    _mutate(koid, _set)


# ── Progress: derived on read (see core/tasks.compute_stages). Only stored signals are
# references (produced links) + task_errors (written on failure). Reassign kickoff.data so
# SQLAlchemy detects the JSONB mutation.

def add_reference(koid, key: str, value):
    """Set data['references'][key] = value (produced link/id). Builds a NEW references dict + reassigns
    data so SQLAlchemy detects the JSONB change — an in-place mutation of the shared nested dict isn't
    tracked (it also corrupts the dirty-check snapshot), so the write would be silently dropped."""
    def _set(k):
        refs = {**(k.data.get("references") or {}), key: value}
        k.data = {**k.data, "references": refs}
    _mutate(koid, _set)


def merge_references(koid, refs: dict):
    """Merge refs into data['references'] (produced links/ids), reassigning data so SQLAlchemy
    detects the JSONB mutation. Used by the async register handler to persist run_register_steps
    output in one write."""
    def _set(k):
        merged = {**(k.data.get("references") or {}), **(refs or {})}
        k.data = {**k.data, "references": merged}
    _mutate(koid, _set)


def record_task_error(koid, stage: str, key: str, message: str):
    """Record a task failure at data['task_errors']['<stage>:<key>'] (rare; only on failure). Builds a
    NEW task_errors dict + reassigns data so SQLAlchemy detects the JSONB change (see add_reference)."""
    def _set(k):
        errs = {**(k.data.get("task_errors") or {}), f"{stage}:{key}": message}
        k.data = {**k.data, "task_errors": errs}
    _mutate(koid, _set)


def clear_task_error(koid, stage: str, key: str):
    """Clear a previously-recorded task failure (data['task_errors']['<stage>:<key>']) on a later
    success, so a retried step doesn't keep showing 'failed'. No-op if the key is absent."""
    def _set(k):
        errs = {**(k.data.get("task_errors") or {})}
        errs.pop(f"{stage}:{key}", None)
        k.data = {**k.data, "task_errors": errs}
    _mutate(koid, _set)


def mark_step_done(koid, key: str):
    """Checkpoint a completion step that produces no ref (gsheet, paaras_dm) so a redelivery
    skips it. data['completed_steps'][key] = True."""
    def _set(k):
        done = {**(k.data.get("completed_steps") or {}), key: True}
        k.data = {**k.data, "completed_steps": done}
    _mutate(koid, _set)


def merge_reminders(koid, updates: dict):
    """Merge updates into kickoff.reminders (the per-(kickoff, day) idempotency ledger). Reassign so
    SQLAlchemy detects the JSONB change."""
    def _set(k):
        k.reminders = {**(k.reminders or {}), **updates}
    _mutate(koid, _set)


def list_awaiting_intake() -> list["Kickoff"]:
    """Active, submitted kickoffs still awaiting the customer's intake form (intake-reminder scan).
    is_draft is excluded: create_draft seeds this status as a placeholder with no intake email sent."""
    with Session(engine) as session:
        stmt = select(Kickoff).where(
            Kickoff.status == KickoffStatus.AWAITING_CUSTOMER_RESPONSE,
            col(Kickoff.is_draft).is_(False),
            col(Kickoff.is_active).is_(True),
        )
        return list(session.exec(stmt).all())


def list_awaiting_consultant(within_date: date) -> list["Kickoff"]:
    """Active kickoffs awaiting a consultant whose kickoff_date is on/before within_date
    (= today + LEAD_DAYS) — the consultant-reminder window."""
    with Session(engine) as session:
        stmt = select(Kickoff).where(
            Kickoff.status == KickoffStatus.AWAITING_CONSULTANT,
            col(Kickoff.is_active).is_(True),
            col(Kickoff.kickoff_date).is_not(None),
            Kickoff.kickoff_date <= within_date,
        )
        return list(session.exec(stmt).all())


def clear_completed_steps(koid, keys: list[str]):
    """Debug/recovery helper: delete the given completed_steps flags so the async handler RE-RUNS
    those steps on the next delivery (a redelivery normally SKIPS a done step). No-op for absent
    keys. Used by the reset endpoint to force an incomplete-step re-drive (e.g. intake_email,
    paaras_dm, assign_channel_add, assign_monday_owners)."""
    def _set(k):
        done = {**(k.data.get("completed_steps") or {})}
        for key in keys or []:
            done.pop(key, None)
        k.data = {**k.data, "completed_steps": done}
    _mutate(koid, _set)


def mark_provisioning_done(koid, tenant_id) -> bool:
    """Completion marker for the Axle provisioning consumer.

    Set ONLY after every completion step has succeeded — it records that the kickoff
    finished provisioning, it is NOT a gate (per-step checkpoints handle dedupe). The
    CAS (provisioning_done_at IS NULL) keeps the write idempotent: returns True the first
    time, False on a redelivery where it is already set (a harmless no-op). Status is
    moved by the caller via set_status_explicit.
    """
    stmt = (
        update(Kickoff)
        .where(Kickoff.koid == koid, Kickoff.provisioning_done_at.is_(None))
        .values(provisioning_done_at=datetime.now(timezone.utc),
                tenant_id=tenant_id,
                updated_at=datetime.now(timezone.utc))
        .returning(Kickoff.koid)
    )
    with Session(engine) as session:
        result = session.execute(stmt)
        session.commit()
        return result.fetchone() is not None


def set_last_error(koid, message: str):
    """Record the last async-path exception into kickoff.data['last_error'] (Phase 2/3 only)."""
    _mutate(koid, lambda k: setattr(k, "data", {**k.data, "last_error": str(message)[:1000]}))


def set_consultant_id(koid, consultant_id: int):
    _mutate(koid, lambda k: setattr(k, "solution_consultant_id", consultant_id))


def set_assignment(koid, consultant_id, analyst_id=None, pod_lead_id=None):
    """Set the three assignment FKs (consultant + optional analyst / pod-lead) at once."""
    def _set(k):
        k.solution_consultant_id = consultant_id
        k.analyst_id = analyst_id
        k.pod_lead_id = pod_lead_id
    _mutate(koid, _set)


def set_assign_removed(koid, emails: list[str]):
    """Stash the emails removed by a re-assignment at data['_assign_removed'] so the next
    run_assign_steps delivery can kick them from the Slack channel. Reassign data so the JSONB
    mutation is detected. Empty list clears the stash."""
    _mutate(koid, lambda k: setattr(k, "data", {**k.data, "_assign_removed": list(emails or [])}))


def set_tenant_id(koid, tenant_id):
    _mutate(koid, lambda k: setattr(k, "tenant_id", tenant_id))


def set_slack_channel_config(koid, extra: dict):
    """Merge extra into kickoff.slack_channel_config (reassign so JSONB mutation is detected)."""
    _mutate(koid, lambda k: setattr(k, "slack_channel_config", {**(k.slack_channel_config or {}), **extra}))


def _company_name_expr():
    # company_name lives under company_details in the grouped blob (denormalized column dropped); fall
    # back to a top-level key for legacy already-flat blobs (matches flatten_groups + the list _item).
    # Drives ops-list sort + search, so it MUST cover both shapes or legacy rows vanish from them.
    return func.coalesce(
        Kickoff.data["company_details"]["company_name"].astext,
        Kickoff.data["company_name"].astext,
    )


_SORT_ALLOWLIST = {"created_at", "updated_at", "status", "kickoff_date"}


def list_kickoffs(
    created_by=None,
    date_from=None,
    date_to=None,
    kickoff_date_from=None,
    kickoff_date_to=None,
    sort="created_at",
    order="desc",
    limit=20,
    status=None,
    skip=0,
    q=None,
    consultant_id=None,
    is_draft=False,
) -> tuple[list[Kickoff], int]:
    """Return (page_rows, total_count) for the kickoff list — filtered, sorted, paginated.
    `is_draft=False` = real ops list; `is_draft=True` = drafts (caller scopes via created_by).
    `q` = customer-name search; `consultant_id` = assigned-to filter."""
    sort_col = (
        _company_name_expr()
        if sort == "company_name"
        else getattr(Kickoff, sort if sort in _SORT_ALLOWLIST else "created_at")
    )
    sort_expr = sort_col.desc() if order == "desc" else sort_col.asc()

    def _apply(stmt):
        stmt = stmt.where(col(Kickoff.is_draft).is_(is_draft))
        stmt = stmt.where(col(Kickoff.is_active).is_(True))  # hide soft-deleted kickoffs
        if created_by is not None:
            stmt = stmt.where(Kickoff.registered_by == created_by)
        if date_from is not None:
            stmt = stmt.where(Kickoff.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Kickoff.created_at <= date_to)
        if kickoff_date_from is not None:
            stmt = stmt.where(Kickoff.kickoff_date >= kickoff_date_from)
        if kickoff_date_to is not None:
            stmt = stmt.where(Kickoff.kickoff_date <= kickoff_date_to)
        if status is not None:
            stmt = stmt.where(Kickoff.status == status)
        if consultant_id is not None:
            stmt = stmt.where(Kickoff.solution_consultant_id == consultant_id)
        if q:
            like = f"%{q}%"  # match the company name (blob) OR the tenant id
            stmt = stmt.where(or_(_company_name_expr().ilike(like), col(Kickoff.tenant_id).ilike(like)))
        return stmt

    with Session(engine) as session:
        total = session.exec(_apply(select(func.count()).select_from(Kickoff))).one()
        rows = session.exec(_apply(select(Kickoff)).order_by(sort_expr).offset(skip).limit(limit)).all()
        return list(rows), total


def distinct_registered_by() -> list[str]:
    """Distinct AE emails (registered_by) across real (non-draft) kickoffs — for the ops-list AE
    filter dropdown. Cheap: hits ix_kickoff_registered_by, O(distinct) not O(rows)."""
    with Session(engine) as session:
        rows = session.exec(
            select(Kickoff.registered_by)
            .where(col(Kickoff.is_draft).is_(False), col(Kickoff.registered_by).is_not(None),
                   col(Kickoff.is_active).is_(True))
            .distinct()
        ).all()
    return sorted(r for r in rows if r)
