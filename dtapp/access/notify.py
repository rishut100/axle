"""Access-automation Slack notify helpers — same shape as kickstart's notify.py (best_effort + a
never-raise logs post), scoped to this module's own channels (recipients.py)."""
import logging
from contextlib import contextmanager

from dtapp.garage.kickstart.clients import slack_client  # reuse kickstart's Slack bot client as-is
from dtapp.access.recipients import recipients

logger = logging.getLogger(__name__)


def send_log(text: str) -> None:
    """Post a step-failure alert to the access-automation Logs channel. NEVER raises."""
    try:
        slack_client.post_message(recipients.logs_channel, text)
    except Exception as e:  # noqa: BLE001 — logging must not raise
        logger.warning("[access] send_log failed: %s", e)


def send_drift_alert(text: str) -> None:
    """Post an offboarding-drift finding to the dedicated drift-alert channel. NEVER raises — a
    notification failure must not stop the verify scan from recording the drift in the DB."""
    try:
        slack_client.post_message(recipients.drift_alert_channel, text)
    except Exception as e:  # noqa: BLE001
        logger.warning("[access] send_drift_alert failed: %s", e)


def notify_summary(text: str) -> None:
    """Post a per-event summary (e.g. 'granted 12/14 tools for jane@...') to the notify channel."""
    try:
        slack_client.post_message(recipients.notify_channel, text)
    except Exception as e:  # noqa: BLE001 — best-effort; the event's own status is the source of truth
        logger.warning("[access] notify_summary failed: %s", e)


@contextmanager
def best_effort(label, aeid):
    """The access-module best-effort guard: run the block, and on failure log + surface it to the
    Logs channel, never re-raising. Mirrors kickstart's notify.best_effort."""
    try:
        yield
    except Exception as e:  # noqa: BLE001 — best-effort; must never propagate
        logger.warning("[access] %s failed for %s: %s", label, aeid, e)
        send_log(f":warning: AccessEvent `{aeid}` — {label} failed: {e}")
