import logging

from dtapp.garage.kickstart.enums import KickoffStatus
from dtapp.garage.core.errors import ConflictError, NotFoundError
from dtapp.garage.kickstart.sqs import flush_outbox
from dtapp.garage.kickstart.repositories import kickoff_repo, consultant_repo
from dtapp.garage.kickstart.kickoff_steps import assignment_emails, run_assign_steps

logger = logging.getLogger(__name__)

# Re-assignment is allowed once the kickoff has reached AWAITING_CONSULTANT — including after DONE
# (Paaras can swap people later). Earlier statuses have nothing to assign yet (no tenant).
_ASSIGNABLE_STATUSES = {
    KickoffStatus.AWAITING_CONSULTANT,
    KickoffStatus.CONSULTANT_ASSIGNED,
    KickoffStatus.DONE,
}


def assign_consultant(koid, consultant, analyst=None, pod_lead=None):
    """(Re)assign the consultant (required) + optional analyst / pod-lead. Resolve emails → consultant
    rows (inserting defensively if absent), set the three FKs, transition to CONSULTANT_ASSIGNED, then
    enqueue the async post-assign work (Slack channel-add + Monday owners). NO external work runs in the
    request — handle_assign runs the tasks and advances the row to DONE.

    Re-assignment / reconciliation: this may be called AGAIN later (even on a DONE kickoff) to change
    who is assigned. We diff the previous assignees against the new set: people no longer assigned are
    stashed (data['_assign_removed']) so the channel-add + Monday-owners steps can kick them from the
    Slack channel and the Monday workspace; the assign step flags are cleared so the post-assign work
    RE-RUNS with the new set (both reconcile: kick removed, then add the current set — idempotent). A
    DONE kickoff briefly goes
    CONSULTANT_ASSIGNED → DONE again. (Calendar invites are out of scope — no calendar integration
    exists; TODO: kick/add the changed people on the kickoff calendar invite once one is built.)

    One person may hold multiple roles (e.g. consultant + pod-lead); the FKs can repeat and the
    downstream channel-add / Monday-owner steps dedupe by email. The Paaras-allowlist authorization
    check lives at the API layer (kickstart_assign_consultant).
    """
    kickoff = kickoff_repo.get_kickoff(koid)
    if kickoff is None:
        raise NotFoundError(f"Kickoff {koid} not found")
    if kickoff.status not in _ASSIGNABLE_STATUSES:
        raise ConflictError(
            f"Kickoff {koid} is not ready for consultant assignment (status {kickoff.status.value})."
        )

    picked = [r for r in (consultant, analyst, pod_lead) if r]
    norm = [r.strip().lower() for r in picked]

    # Diff old vs new assignees BEFORE overwriting the FKs. removed = old - new → kicked from Slack.
    old_emails = {e.strip().lower() for e in assignment_emails(kickoff)}
    new_emails = set(norm)
    removed = sorted(old_emails - new_emails)

    def resolve(email):
        if not email:
            return None
        c = consultant_repo.get_consultant_by_email(email)
        if c is None:
            # Picker normally only sends known consultants; defensive upsert for safety.
            c = consultant_repo.upsert_consultant(name=email, email=email)
        return c.id

    if not kickoff_repo.assign_consultant_atomic(
        koid,
        resolve(consultant), resolve(analyst), resolve(pod_lead),
        removed,
        ["assign_channel_add", "assign_monday_owners", "assign_deck_linear"],
    ):
        raise NotFoundError(f"Kickoff {koid} not found")
    flush_outbox(koid=koid)
    return koid


def handle_assign(message: dict):
    """Consume an Axle-queue {koid, type:"assign"} message: run the post-assign-consultant work —
    add the assigned consultant / analyst / pod-lead to the internal Slack channel + as Monday
    workspace owners — then advance CONSULTANT_ASSIGNED → DONE.

    Effectively-once / resumable under SQS redelivery: each task is guarded by a completed-step flag
    (assign_channel_add / assign_monday_owners) and the Slack invite is itself idempotent, so a
    redelivery skips finished work. Status advances ONLY after both tasks succeed, so a mid-flow
    failure leaves the row CONSULTANT_ASSIGNED and a redelivery finishes the remaining work. Raises on
    any failure (after recording last_error) so Axle's SQS queue redelivers (bounded; no DLQ — a stuck
    CONSULTANT_ASSIGNED kickoff surfaces via monitoring; recovery is a re-enqueue)."""
    koid = message.get("koid")
    if not koid:
        logger.warning("[kickstart] assign message missing koid: %s", message)
        return

    kickoff = kickoff_repo.get_kickoff(koid)  # CONSULTANT_ASSIGNED rows are is_draft=False → fetchable
    if kickoff is None:
        logger.warning("[kickstart] assign for unknown koid %s — skipping", koid)
        return

    try:
        run_assign_steps(koid, kickoff)
    except Exception as e:  # noqa: BLE001 — record + re-raise so SQS redelivers (bounded; no DLQ)
        kickoff_repo.set_last_error(koid, e)
        logger.exception("[kickstart] assign failed for %s", koid)
        raise
    # advance ONLY after the post-assign work succeeded — a mid-flow failure leaves it CONSULTANT_ASSIGNED.
    kickoff_repo.set_status_explicit(koid, KickoffStatus.DONE)
    logger.info("[kickstart] %s assign complete → DONE", koid)
