"""Offboarding side-effects — run by the queue consumer for an AccessEvent(event_type="offboard").
Mirrors grant.py's shape. After a successful api-method revoke, schedules the drift-verification
re-check (decision #7) rather than trusting the revoke call's 200 OK at face value."""
import logging
from datetime import datetime, timedelta, timezone

from dtapp.access.access_steps._runner import run_step
from dtapp.access.access_steps.common import get_client
from dtapp.access.clients import linear_client
from dtapp.access.constants import REVOKE_VERIFY_DELAY_HOURS
from dtapp.access.recipients import recipients
from dtapp.access.repositories import access_event_repo
# See grant.py's import comment — same top-level-bot requirement for the reaction listener to work.
from dtapp.services.slack_service import post_access_tool_message

logger = logging.getLogger(__name__)


def _revoke_api_tool(aeid, tool, entry):
    client = get_client(entry["client_name"], {**entry.get("client_config", {}), "tool_name": tool})
    if hasattr(client, "remove_member"):
        # Bespoke clients (GitHub, Okta) take an email and resolve their own identifier internally.
        client.remove_member(entry["employee_email"])
        scim_user_id = entry.get("scim_user_id")  # nothing new to capture; keep whatever grant stored
    else:
        scim_user_id = entry.get("scim_user_id")
        if not scim_user_id and hasattr(client, "find_user_by_email"):
            found = client.find_user_by_email(entry["employee_email"])
            scim_user_id = (found or {}).get("id")
        client.deactivate_user(scim_user_id)
    access_event_repo.set_checklist_item(aeid, tool, status="revoked", scim_user_id=scim_user_id, error=None)
    return f"revoked {tool}"


def _revoke_manual_tool(aeid, tool, entry):
    team = entry.get("linear_team") or recipients.default_linear_team
    state = entry.get("linear_state") or recipients.default_linear_state
    issue = linear_client.create_issue(
        team_id=team, state_id=state,
        title=f"Revoke {tool} access — {entry['employee_name']} ({entry['employee_email']})",
        description=(f"Leaver: {entry['employee_name']} <{entry['employee_email']}>\n"
                     f"Team: {entry.get('team')}\n\nPlease revoke {tool} access, then react ✅ once done."),
    )
    access_event_repo.merge_references(aeid, {f"{tool}_linear_issue_id": issue.id})
    owner_target = entry.get("owner_slack_id")  # see grant.py — email alone can't open a Slack DM
    if owner_target:
        post_access_tool_message(
            owner_target,
            f":rotating_light: Please REVOKE *{tool}* access for {entry['employee_name']} "
            f"({entry['employee_email']}). Ticket: {issue.url}\nReact :white_check_mark: here once done.",
            aeid, tool,
        )
    access_event_repo.set_checklist_item(aeid, tool, status="ticket_open", linear_issue_id=issue.id,
                                         linear_team_id=team)
    return f"ticket opened for {tool} ({issue.identifier})"


def run_revoke_steps(aeid: str, checklist: dict):
    """See run_grant_steps' docstring — same re-read-completed_steps-from-DB idempotency discipline."""
    event = access_event_repo.get_event(aeid)
    data = {"completed_steps": event.completed_steps if event else {}}
    any_api_tool = False
    for tool, entry in checklist.items():
        flag = f"revoke:{tool}"
        if entry.get("method") == "api":
            any_api_tool = True
        fn = _revoke_api_tool if entry.get("method") == "api" else _revoke_manual_tool
        run_step(aeid, data, flag, lambda t=tool, e=entry, f=fn: f(aeid, t, e),
                best_effort=True, task_key=tool, stage="revoke")
    if any_api_tool:
        access_event_repo.set_revoke_verify_due(
            aeid, datetime.now(timezone.utc) + timedelta(hours=REVOKE_VERIFY_DELAY_HOURS))
    final_status = access_event_repo.rollup_status(aeid)
    access_event_repo.set_status(aeid, final_status)
