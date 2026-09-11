"""Request-thin orchestration: validate -> resolve matrix -> build checklist -> write row + outbox (1
txn) -> flush. NO external (tool/Linear/Slack) calls happen here — those are the queue consumer's job
(access_steps/grant.py, revoke.py). Mirrors kickstart's kickoff_service.handle_register shape.
"""
import logging

from dtapp.access.matrix import matrix_repo
from dtapp.access.repositories import access_event_repo
from dtapp.access.schemas.access_event import NormalizedAccessEvent
from dtapp.access.sqs import flush_outbox

logger = logging.getLogger(__name__)


def _build_checklist(event: NormalizedAccessEvent) -> dict:
    """Resolve the role->tool->owner matrix for this event's (team, role) and build the initial
    per-tool checklist. Every entry is denormalized with employee_email/employee_name/team so
    access_steps.grant/revoke can act on a tool without an extra DB round-trip per step."""
    entries = matrix_repo.resolve_for(event.team, event.role)
    checklist = {}
    for entry in entries:
        checklist[entry.tool_name] = {
            "method": entry.method,
            "client_name": entry.client_name,
            "client_config": entry.client_config,
            "owner_email": entry.owner_email,
            "owner_slack_id": entry.owner_slack_id,
            "status": "pending",
            "employee_email": event.employee_email,
            "employee_name": event.employee_name,
            "team": event.team,
        }
    return checklist


def ingest_event(event: NormalizedAccessEvent, *, publish: bool = True) -> str:
    """Idempotent by external_id: a re-delivered/duplicate webhook call for the same person+action
    returns the existing aeid rather than creating a second row. Returns the AccessEvent's aeid.

    publish=False skips queuing a real SQS message entirely (see create_event_atomic's docstring for
    why this exists — a local test/coverage script calling ingest_event with publish=True while ANY
    real-credentialed consumer is running, even a separate local dev server, WILL be processed for
    real by that consumer independent of what the calling script does). Pass publish=False for any
    ingestion that's purely exercising matrix-resolution/checklist-building logic, then call
    run_grant_steps/run_revoke_steps directly in-process to test dispatch without ever touching SQS."""
    existing = access_event_repo.get_by_external_id(event.external_id)
    if existing is not None:
        logger.info("[access] duplicate ingest for external_id=%s -> existing %s", event.external_id, existing.aeid)
        return existing.aeid

    checklist = _build_checklist(event)
    if not checklist:
        logger.warning("[access] no matrix entries resolved for team=%r role=%r (external_id=%s) — "
                       "nothing to grant/revoke; check access_tool_matrix", event.team, event.role, event.external_id)

    outbox_type = "onboard" if event.event_type == "onboard" else "offboard"
    # Outbox message `type` drives which step-runner the consumer dispatches to — reuse the SAME two
    # values as the SQS body's "type" field (queue.py's handlers dict keys are "grant"/"revoke", so map
    # onboard->grant, offboard->revoke here rather than carrying event_type through as a third name).
    mtype = "grant" if event.event_type == "onboard" else "revoke"
    created = access_event_repo.create_event_atomic(
        external_id=event.external_id, event_type=event.event_type,
        employee_email=event.employee_email, employee_name=event.employee_name,
        team=event.team, role=event.role, manager_email=event.manager_email,
        effective_date=event.effective_date, checklist=checklist, outbox_type=mtype,
        publish=publish,
    )
    if publish:
        flush_outbox(created.aeid)  # fast-path publish right after the commit (best-effort, never raises)
    logger.info("[access] ingested %s event %s for %s (%d tool(s) in checklist, publish=%s)",
               event.event_type, created.aeid, event.employee_email, len(checklist), publish)
    return created.aeid
