"""Kickstart Slack mention/alert helpers — @-mention markup + the never-raise Kickstart Logs channel.
Channel routing comes from recipients (env-resolved: prod real channels, non-prod Shahbaz Dev). Plain
channel posts go straight through slack_client.post_message (raise-on-fail, caught by the caller's guard);
send_log NEVER raises — it runs on best-effort failure paths and must not break them."""
import logging
from contextlib import contextmanager

from dtapp.garage.kickstart.clients import slack_client
from dtapp.garage.kickstart.recipients import recipients

logger = logging.getLogger(__name__)


def mention(user_id: str) -> str:
    """Slack @-mention markup for a user id (e.g. '<@U012ABC>')."""
    return f"<@{user_id}>"


def ae_mention(email: str, cache: dict | None = None) -> str:
    """Resolve the AE's Slack id from their email → @-mention markup, degrading to the bare email (then
    'the AE') when it can't be resolved — a lookup miss must never block a reminder. Pass a dict `cache`
    to memoize the resolve across a scan (the same AE recurs across kickoffs → one lookup, not N)."""
    if not email:
        return "the AE"
    if cache is not None and email in cache:
        uid = cache[email]
    else:
        uid = slack_client.resolve_user_id(email)
        if cache is not None:
            cache[email] = uid
    return mention(uid) if uid else email


def send_log(text: str) -> None:
    """Post a step-failure / reminder-failure alert to the Kickstart Logs channel. NEVER raises — a
    logging-channel failure must not break the caller's best-effort path."""
    try:
        slack_client.post_message(recipients.logs_channel, text)
    except Exception as e:  # noqa: BLE001 — logging must not raise
        logger.warning("[kickstart] send_log failed: %s", e)


@contextmanager
def best_effort(label, koid):
    """The single kickstart best-effort guard: run the block, and on failure log + surface it to
    Kickstart Logs, never re-raising. Shared by the async step runner and the reminder scans."""
    try:
        yield
    except Exception as e:  # noqa: BLE001 — best-effort; must never propagate
        logger.warning("[kickstart] %s failed for %s: %s", label, koid, e)
        send_log(f":warning: Kickoff `{koid}` — {label} failed: {e}")
