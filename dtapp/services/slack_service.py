import os
from datetime import datetime, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dtapp.main import app

SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
SLACK_TENANT_ACCESS_CHANNEL = os.environ.get('SLACK_TENANT_ACCESS_CHANNEL', 'tenant-access-requests')

slack_client = WebClient(token=SLACK_BOT_TOKEN)


def _format_validity(validity):
    try:
        expiry = datetime.fromisoformat(str(validity))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        delta = expiry - datetime.now(timezone.utc)
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "expired"
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        if days > 0:
            return f"{days} day{'s' if days != 1 else ''}"
        if hours > 0:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    except Exception:
        return str(validity)


def post_tenant_access_request(request_id, tenant_id, user_email, validity, reason, v3=False):
    version_label = "v3" if v3 else "v2"
    retool_app = "tenant_access_v3" if v3 else "tenant_access"
    retool_link = f"https://your-retool-domain.retool.com/app/{retool_app}#id={request_id}"
    validity_display = _format_validity(validity)
    try:
        slack_client.chat_postMessage(
            channel=SLACK_TENANT_ACCESS_CHANNEL,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*🔔 Tenant Access Request ({version_label})*\n"
                            f"*Tenant ID:* {tenant_id}\n"
                            f"*User:* {user_email}\n"
                            f"*Validity:* {validity_display}\n"
                            f"*Reason:* {reason}\n"
                            f"*Link to approve:* <{retool_link}|Review Request>"
                        ),
                    },
                },
            ],
            metadata={
                "event_type": "tenant_access_request",
                "event_payload": {
                    "request_id": str(request_id),
                    "v3": v3,
                },
            },
        )
        app.logger.info(f"Slack notification posted for tenant access request {request_id}")
    except SlackApiError as e:
        app.logger.error(f"Failed to post Slack message for request {request_id}: {e.response['error']}")


def post_access_tool_message(target_id, text, aeid, tool):
    """Notify a manual-tool owner for an access-automation event (ENG-90754), tagged with the same
    kind of metadata `post_tenant_access_request` uses — so the existing `handle_reaction_added`
    listener (this module's bot/app, wired to /slack/events) can read it back and mark the tool done
    on a ✅ reaction. Deliberately uses THIS bot (not garage/kickstart's separate Slack app/token) —
    the reaction listener only has metadata-read access to DMs/channels ITS OWN bot posted into, so the
    notify and the reaction-read must be the same bot. `target_id`: a channel id (C…/G…) or a user id
    (opens a DM). Returns the posted message's ts, or None on failure (best-effort — caller already
    creates a Linear ticket as the durable audit trail, so a Slack-post miss must not block anything)."""
    try:
        channel = target_id
        if not str(target_id).startswith(("C", "G")):
            channel = slack_client.conversations_open(users=target_id)["channel"]["id"]
        resp = slack_client.chat_postMessage(
            channel=channel,
            text=text,
            metadata={
                "event_type": "access_event",
                "event_payload": {"aeid": aeid, "tool": tool},
            },
        )
        return resp.get("ts")
    except SlackApiError as e:
        app.logger.error(f"Failed to post access-tool Slack message for {aeid}/{tool}: {e.response['error']}")
        return None
