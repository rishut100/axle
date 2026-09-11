"""Onboarding side-effects — run by the queue consumer for an AccessEvent(event_type="onboard").
For each checklist entry: method="api" -> call the tool's client to create/invite the user;
method="manual" -> create a Linear sub-issue + Slack-notify the tool's human owner (audit trail; "done"
is later signaled by their ✅ reaction, handled in views/slack.py, not by this module directly).
"""
import logging
from datetime import datetime, timedelta, timezone

from dtapp.access.access_steps._runner import run_step
from dtapp.access.access_steps.common import get_client
from dtapp.access.clients import linear_client
from dtapp.access.constants import GRANT_VERIFY_DELAY_HOURS
from dtapp.access.recipients import recipients
from dtapp.access.repositories import access_event_repo
# Deliberately the TOP-LEVEL Slack bot (dtapp.services.slack_service), not garage/kickstart's separate
# Slack app/token — the ✅-reaction "done" listener (views/slack.py::handle_reaction_added) only has
# metadata-read access to messages ITS OWN bot posted; see post_access_tool_message's docstring.
from dtapp.services.slack_service import post_access_tool_message

logger = logging.getLogger(__name__)


def _grant_api_tool(aeid, tool, entry):
    client = get_client(entry["client_name"], {**entry.get("client_config", {}), "tool_name": tool})
    # Bespoke clients (GitHub, Okta) expose invite_member(email) — check that FIRST, since some bespoke
    # clients (OktaCorpClient) also happen to define create_user as an internal helper; only ScimClient
    # is meant to be driven via create_user directly.
    if hasattr(client, "invite_member"):
        user = client.invite_member(entry["employee_email"])
    else:
        user = client.create_user(entry["employee_email"], entry["employee_name"])
    scim_user_id = (user or {}).get("id") if isinstance(user, dict) else None
    access_event_repo.set_checklist_item(aeid, tool, status="granted", scim_user_id=scim_user_id, error=None)
    return f"granted {tool}"


def _grant_manual_tool(aeid, tool, entry):
    team = entry.get("linear_team") or recipients.default_linear_team
    state = entry.get("linear_state") or recipients.default_linear_state
    issue = linear_client.create_issue(
        team_id=team, state_id=state,
        title=f"Grant {tool} access — {entry['employee_name']} ({entry['employee_email']})",
        description=(f"New hire: {entry['employee_name']} <{entry['employee_email']}>\n"
                     f"Team: {entry.get('team')}\n\nPlease grant {tool} access, then react ✅ on the "
                     f"linked Slack message (or this ticket) once done."),
    )
    access_event_repo.merge_references(aeid, {f"{tool}_linear_issue_id": issue.id})
    # Slack DM needs a real Slack user/channel id (conversations_open takes a user id, not an email) —
    # owner_email alone (no owner_slack_id on the matrix row) means the Linear ticket is the only
    # notification; that's a matrix data-quality gap to fix, not something to paper over with a guess.
    owner_target = entry.get("owner_slack_id")
    if owner_target:
        post_access_tool_message(
            owner_target,
            f":wave: Please grant *{tool}* access for {entry['employee_name']} "
            f"({entry['employee_email']}). Ticket: {issue.url}\nReact :white_check_mark: here once done.",
            aeid, tool,
        )
    # linear_team_id is stashed so the ✅-reaction / manual-complete handlers can close this exact
    # issue later without re-deriving which team it lives in (recipients' non-prod default could
    # differ from a future matrix-row override, so re-deriving at close-time isn't safe).
    access_event_repo.set_checklist_item(aeid, tool, status="ticket_open", linear_issue_id=issue.id,
                                         linear_team_id=team)
    return f"ticket opened for {tool} ({issue.identifier})"


def run_grant_steps(aeid: str, checklist: dict):
    """`checklist` is the AccessEvent.checklist dict at dispatch time; each entry additionally carries
    employee_email/employee_name/team (denormalized onto every checklist item at build time in
    event_service, so a step here needs no extra DB round-trip to know who it's granting access for).

    Re-reads the event's `completed_steps` from the DB (not the caller's snapshot) right before
    dispatch, so a redelivery correctly skips whatever a prior attempt already finished — the whole
    point of run_step's idempotent-flag check."""
    event = access_event_repo.get_event(aeid)
    data = {"completed_steps": event.completed_steps if event else {}}
    any_api_tool = False
    for tool, entry in checklist.items():
        flag = f"grant:{tool}"
        if entry.get("method") == "api":
            any_api_tool = True
        fn = _grant_api_tool if entry.get("method") == "api" else _grant_manual_tool
        run_step(aeid, data, flag, lambda t=tool, e=entry, f=fn: f(aeid, t, e),
                best_effort=True, task_key=tool, stage="grant")
    if any_api_tool:
        access_event_repo.set_grant_verify_due(
            aeid, datetime.now(timezone.utc) + timedelta(hours=GRANT_VERIFY_DELAY_HOURS))
    final_status = access_event_repo.rollup_status(aeid)
    access_event_repo.set_status(aeid, final_status)
