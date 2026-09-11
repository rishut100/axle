import threading
from datetime import datetime, timezone

from dtapp.main import app
from dtapp.services.slack_service import slack_client
from dtapp.views.tenant_access import update_tenant_access, get_tenant_access, create_tenant_access

AUTHORIZED_APPROVERS = {
    'alokg@drivetrain.ai',
    'tark@drivetrain.ai',
    'paaras@drivetrain.ai',
    'saurav@drivetrain.ai',
    'jason@drivetrain.ai',
}


def handle_reaction_added(event):
    app.logger.info(f"Handling Slack reaction_added event: {event}")
    if event.get('reaction') != 'white_check_mark':
        return

    item = event.get('item', {})
    if item.get('type') != 'message':
        return

    channel = item['channel']
    ts = item['ts']
    reactor_id = event.get('user')

    # Fetch the original message including hidden metadata
    app.logger.info(f"Fetching Slack message for channel={channel}, ts={ts}")
    history = slack_client.conversations_history(
        channel=channel,
        latest=ts,
        inclusive=True,
        limit=1,
        include_all_metadata=True,
    )
    messages = history.get('messages', [])
    if not messages:
        app.logger.warning("Slack reaction: could not fetch original message")
        return

    metadata = messages[0].get('metadata', {})
    event_type = metadata.get('event_type')
    payload = metadata.get('event_payload', {})

    if event_type == 'access_event':
        _handle_access_event_reaction(payload, reactor_id)
        return

    request_id = payload.get('request_id')
    v3 = payload.get('v3', False)

    if not request_id:
        app.logger.warning("Slack reaction: no request_id found in message metadata")
        return

    # Resolve approver email from Slack user profile
    user_info = slack_client.users_info(user=reactor_id)
    approved_by = user_info['user']['profile'].get('email', reactor_id)

    if approved_by not in AUTHORIZED_APPROVERS:
        app.logger.info(f"Slack reaction ignored: {approved_by} is not an authorized approver")
        return

    app.logger.info(f"Slack reaction approval: request_id={request_id}, approved_by={approved_by}, v3={v3}")
    update_tenant_access(ids=[request_id], status=1, approved_by=approved_by, v3=v3)


def _handle_access_event_reaction(payload, reactor_id):
    """A tool owner reacted ✅ on their access-automation notification (ENG-90754) — the manual-tool
    'done' signal (see access/CLAUDE.md, decision #6). No AUTHORIZED_APPROVERS gate here (unlike
    tenant-access approval): anyone who can react on a DM sent to a specific owner is, by construction,
    that owner (or someone they forwarded it to, which is an acceptable trust boundary for "I granted
    this tool's access", unlike approving a customer tenant-access grant)."""
    aeid = payload.get('aeid')
    tool = payload.get('tool')
    if not aeid or not tool:
        app.logger.warning(f"Slack reaction: incomplete access_event payload {payload}")
        return
    from dtapp.access.repositories import access_event_repo
    from dtapp.access.notify import best_effort
    from dtapp.access.clients import linear_client
    event = access_event_repo.get_event(aeid)
    if event is None or tool not in event.checklist:
        app.logger.warning(f"Slack reaction: no matching access_event/tool for aeid={aeid} tool={tool}")
        return
    user_info = slack_client.users_info(user=reactor_id)
    completed_by = user_info['user']['profile'].get('email', reactor_id)
    app.logger.info(f"Access-event tool completion via Slack reaction: aeid={aeid}, tool={tool}, by={completed_by}")
    access_event_repo.set_checklist_item(aeid, tool, status="done", completed_by=completed_by)
    access_event_repo.set_status(aeid, access_event_repo.rollup_status(aeid))

    # Auto-close the Linear ticket itself (not just our internal tracking) — best-effort: a Linear
    # API hiccup here must not undo the completion we just recorded.
    entry = event.checklist.get(tool, {})
    issue_id, team_id = entry.get("linear_issue_id"), entry.get("linear_team_id")
    if issue_id and team_id:
        with best_effort(f"close_linear_issue:{tool}", aeid):
            linear_client.close_issue(issue_id, team_id)


def handle_approve_all_command(payload):
    user_id = payload.get('user_id')
    command_text = payload.get('text', '').strip().lower()
    v3 = 'v3' in command_text

    # Resolve approver email from Slack user profile
    user_info = slack_client.users_info(user=user_id)
    approved_by = user_info['user']['profile'].get('email', user_id)

    if approved_by not in AUTHORIZED_APPROVERS:
        app.logger.info(f"Slack approve-all command ignored: {approved_by} is not an authorized approver")
        return "⛔ You are not authorized to approve tenant access requests."

    pending = get_tenant_access(v3=v3, status=0)
    if not pending:
        return "No pending tenant access requests."

    ids = [r['id'] for r in pending]
    app.logger.info(f"Slack slash command: approving {len(ids)} requests by {approved_by}, v3={v3}")
    update_tenant_access(ids=ids, status=1, approved_by=approved_by, v3=v3)

    return f"✅ Approved {len(ids)} tenant access request(s)."


TENANT_ACCESS_MODAL_CALLBACK_ID = "tenant_access_request_modal"


def _is_v3(tenant_id):
    try:
        return int(tenant_id) >= 400
    except (TypeError, ValueError):
        return False


def handle_request_access_command(payload):
    trigger_id = payload.get('trigger_id')
    app.logger.info(f"Opening tenant access request modal for trigger_id={trigger_id}")

    slack_client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": TENANT_ACCESS_MODAL_CALLBACK_ID,
            "title": {"type": "plain_text", "text": "Request Tenant Access"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "tenant_id_block",
                    "label": {"type": "plain_text", "text": "Tenant ID"},
                    "element": {
                        "type": "number_input",
                        "action_id": "tenant_id",
                        "is_decimal_allowed": False,
                        "placeholder": {"type": "plain_text", "text": "e.g. 123"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "user_email_block",
                    "label": {"type": "plain_text", "text": "User Email"},
                    "hint": {"type": "plain_text", "text": "Only @drivetrain.ai addresses are accepted."},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "user_email",
                        "placeholder": {"type": "plain_text", "text": "user@drivetrain.ai"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "validity_block",
                    "label": {"type": "plain_text", "text": "Access Valid Until"},
                    "element": {
                        "type": "datetimepicker",
                        "action_id": "validity",
                    },
                },
                {
                    "type": "input",
                    "block_id": "reason_block",
                    "label": {"type": "plain_text", "text": "Reason"},
                    "hint": {"type": "plain_text", "text": "Minimum 20 characters."},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "reason",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "Briefly describe why access is needed"},
                    },
                },
            ],
        },
    )


def _process_modal_submission(user_id, tenant_id, user_email, validity, reason, v3):
    try:
        result = create_tenant_access(
            tenant_id=tenant_id,
            user_email=user_email,
            validity=validity,
            reason=reason,
            v3=v3,
        )
        app.logger.info(f"Tenant access request created via Slack modal: {result}")
        print(result)
        request_id = result[0].get('id') if result else None
        msg = "✅ Tenant access request submitted successfully!"
        if request_id:
            msg += f" (Tenant ID: {tenant_id})"
    except Exception as e:
        app.logger.error(f"Failed to create tenant access from Slack modal: {e}")
        msg = f"❌ Failed to submit tenant access request: {e}"

    try:
        slack_client.chat_postMessage(channel=user_id, text=msg)
    except Exception as e:
        app.logger.error(f"Failed to DM user {user_id} after modal submission: {e}")


def handle_modal_submission(payload):
    state_values = payload.get('view', {}).get('state', {}).get('values', {})
    user_id = payload.get('user', {}).get('id')

    tenant_id = state_values['tenant_id_block']['tenant_id']['value']
    user_email = state_values['user_email_block']['user_email']['value']
    validity_ts = state_values['validity_block']['validity']['selected_date_time']
    reason = state_values['reason_block']['reason']['value']

    errors = {}
    if not user_email.endswith('@drivetrain.ai'):
        errors['user_email_block'] = "Email must be a @drivetrain.ai address."
    if len(reason.strip()) < 20:
        errors['reason_block'] = "Reason must be at least 20 characters."
    if errors:
        return {"response_action": "errors", "errors": errors}

    validity = datetime.fromtimestamp(validity_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    v3 = _is_v3(tenant_id)

    app.logger.info(f"Slack modal submission: tenant_id={tenant_id}, user_email={user_email}, validity={validity}, v3={v3}")

    threading.Thread(
        target=_process_modal_submission,
        args=(user_id, tenant_id, user_email, validity, reason, v3),
        daemon=True,
    ).start()

    return None
