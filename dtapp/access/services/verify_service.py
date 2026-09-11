"""Post-action verification — a CRON DB-scan (registered on the shared CronScheduler in main.py, same
pattern as kickstart's reminders.py), not a queued message: it needs to survive well past any single
SQS message's visibility timeout and re-run on a schedule, which a periodic scan handles more simply
than a delayed-message chain.

Covers BOTH directions:
- Offboarding (decision #7/#8): confirm a "revoked" tool is actually inactive — a revoke call
  returning 200 doesn't guarantee the tool's own state actually changed.
- Onboarding (added after real-world testing surfaced the gap): confirm a "granted" tool is actually
  active — same reasoning, opposite direction. Drift here means someone THINKS they have access and
  don't yet (an availability problem, not a security one — escalated to logs, not the drift-alert
  security channel).
"""
import logging
from datetime import datetime, timezone

from dtapp.access.access_steps.common import get_client
from dtapp.access.notify import best_effort, send_drift_alert, send_log
from dtapp.access.repositories import access_event_repo

logger = logging.getLogger(__name__)

# Statuses get_user_status can return that count as "confirmed inactive" (revoke path) — clients use
# slightly different vocabulary (SCIM: "inactive"; GitHub: "removed").
_INACTIVE_STATUSES = ("inactive", "removed")


def _resolve_identifier(client, entry: dict):
    """SCIM clients key on the stored scim_user_id (the SCIM resource id); bespoke email-based clients
    (GitHub, Okta) just take the email directly — see access/CLAUDE.md for why these differ."""
    if hasattr(client, "invite_member"):  # bespoke, email-based (same check grant.py uses)
        return entry["employee_email"]
    return entry.get("scim_user_id")


def _check_one_tool(event, tool, entry, *, expect_status: str) -> None:
    """expect_status: 'revoked' (offboard direction, expect the tool to report inactive) or 'granted'
    (onboard direction, expect the tool to report active)."""
    if entry.get("method") != "api" or entry.get("status") != expect_status:
        return
    client = get_client(entry["client_name"], {**entry.get("client_config", {}), "tool_name": tool})
    identifier = _resolve_identifier(client, entry)
    status = client.get_user_status(identifier) if identifier else "unknown"

    if expect_status == "revoked":
        if status not in _INACTIVE_STATUSES:
            access_event_repo.record_drift(event.aeid, tool, status)
            send_drift_alert(
                f":rotating_light: *Offboarding drift* — {event.employee_name} ({event.employee_email}) "
                f"was revoked from *{tool}* but the tool still reports status=`{status}`. Please "
                f"investigate and confirm manually.")
            logger.warning("[access] revoke drift: %s still %s for %s (event %s)",
                           tool, status, event.employee_email, event.aeid)
    else:  # expect_status == "granted"
        if status in _INACTIVE_STATUSES or status == "unknown":
            access_event_repo.record_drift(event.aeid, tool, status)
            send_log(
                f":warning: *Onboarding drift* — {event.employee_name} ({event.employee_email}) was "
                f"granted *{tool}* but the tool reports status=`{status}` (not yet active). Please "
                f"check — they may not actually have access yet.")
            logger.warning("[access] grant drift: %s still %s for %s (event %s)",
                           tool, status, event.employee_email, event.aeid)


def run_revoke_verification():
    """Cron entry point (REVOKE_VERIFY_JOB_ID). Picks up every AccessEvent whose revoke_verify_due_at
    has passed and hasn't been verified yet."""
    due = access_event_repo.list_due_for_verify(datetime.now(timezone.utc))
    if not due:
        return
    logger.info("[access] revoke-verify scan: %d event(s) due", len(due))
    for event in due:
        for tool, entry in event.checklist.items():
            with best_effort(f"verify_revoke:{tool}", event.aeid):
                _check_one_tool(event, tool, entry, expect_status="revoked")
        access_event_repo.mark_verified(event.aeid)


def run_grant_verification():
    """Cron entry point (GRANT_VERIFY_JOB_ID) — the onboarding-side mirror of run_revoke_verification.
    Picks up every AccessEvent whose grant_verify_due_at has passed and hasn't been verified yet."""
    due = access_event_repo.list_due_for_grant_verify(datetime.now(timezone.utc))
    if not due:
        return
    logger.info("[access] grant-verify scan: %d event(s) due", len(due))
    for event in due:
        for tool, entry in event.checklist.items():
            with best_effort(f"verify_grant:{tool}", event.aeid):
                _check_one_tool(event, tool, entry, expect_status="granted")
        access_event_repo.mark_grant_verified(event.aeid)
