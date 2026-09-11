"""Kickstart daily reminder scans (Phase 3) — cron-driven, run by dtapp.cron_scheduler.
Each scan is idempotent per (kickoff, day) via kickoff.reminders, so a double-fire (multi-replica)
or a pod-restart catch-up never double-sends. A per-kickoff failure is logged to Kickstart Logs and
never aborts the scan."""
import logging
from datetime import date, timedelta

from dtapp.garage.core import constants
from dtapp.garage.core.config import settings
from dtapp.garage.kickstart import notify
from dtapp.garage.kickstart.clients import sendgrid_client, slack_client
from dtapp.garage.kickstart.recipients import recipients
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart.schemas.kickoff import flatten_groups

logger = logging.getLogger(__name__)

# reminders-ledger keys (kickoff.reminders JSONB) — one per daily channel + the email seq counter.
_INTAKE = "intake"
_INTAKE_EMAIL = "intake_email"
_INTAKE_EMAIL_SEQ = "intake_email_seq"
_CONSULTANT = "consultant"


def _skip_today() -> bool:
    """True when business-days-only is on and today is a weekend."""
    return constants.REMINDER_BUSINESS_DAYS_ONLY and date.today().weekday() >= 5


def _already(kickoff, key: str, today_iso: str) -> bool:
    return (kickoff.reminders or {}).get(key) == today_iso


def run_intake_reminders() -> None:
    """For every kickoff still awaiting the customer's intake form: nudge the AE on Slack (Kickstart
    Notifications) AND email the customer on the original intake thread. Both are best-effort and
    independent; each is sent at most once per day. Skips the registration day (START_LAG_DAYS)."""
    if _skip_today():
        return
    today = date.today()
    today_iso = today.isoformat()
    ae_cache: dict = {}  # scan-scoped email→id memo: the same AE recurs across kickoffs → one Slack lookup
    for k in kickoff_repo.list_awaiting_intake():
        anchor = k.submitted_at or k.created_at
        if anchor and (today - anchor.date()).days < constants.INTAKE_REMINDER_START_LAG_DAYS:
            continue
        intake_due = not _already(k, _INTAKE, today_iso)
        email_due = bool(settings.sendgrid_api_key) and not _already(k, _INTAKE_EMAIL, today_iso)
        if not (intake_due or email_due):
            continue  # nothing left to send today for this kickoff — skip the blob flatten
        flat = flatten_groups(k.data)
        company = flat.get("company_name") or k.koid
        link = (k.data.get("references") or {}).get("intake_link") or ""
        # Slack nudge to the AE (independent of the email)
        if intake_due:
            with notify.best_effort("intake slack reminder", k.koid):
                who = notify.ae_mention(k.registered_by, ae_cache)
                text = (f"{who} Reminder: *{company}* hasn't submitted their onboarding intake form yet."
                        + (f" Intake link: {link}" if link else ""))
                slack_client.post_message(recipients.notify_channel, text)
                kickoff_repo.merge_reminders(k.koid, {_INTAKE: today_iso})
                logger.info("[kickstart] intake slack reminder sent for %s", k.koid)
        # Threaded email nudge to the customer (skip silently without a recipient = local/dryrun)
        if email_due and flat.get("onboarding_contact_email"):
            if not link:
                # No CTA link → don't send a broken email; leave the ledger so it sends once the link is set.
                notify.send_log(f":warning: Kickoff `{k.koid}` — intake email reminder skipped: no intake link")
            else:
                with notify.best_effort("intake email reminder", k.koid):
                    seq = int((k.reminders or {}).get(_INTAKE_EMAIL_SEQ) or 0) + 1
                    sendgrid_client.send_intake_reminder_email(k.koid, flat, link, seq)
                    kickoff_repo.merge_reminders(k.koid, {_INTAKE_EMAIL: today_iso, _INTAKE_EMAIL_SEQ: seq})
                    logger.info("[kickstart] intake email reminder %d sent for %s", seq, k.koid)


def run_consultant_reminders() -> None:
    """Nudge Paaras (Slack, the kickoff's own internal channel) to assign a consultant, from
    (kickoff_date - LEAD_DAYS) until one is assigned. At most one nudge per day."""
    if _skip_today():
        return
    today = date.today()
    today_iso = today.isoformat()
    within = today + timedelta(days=constants.CONSULTANT_REMINDER_LEAD_DAYS)
    for k in kickoff_repo.list_awaiting_consultant(within):
        if _already(k, _CONSULTANT, today_iso):
            continue
        channel_id = (k.slack_channel_config or {}).get("channel_id")
        if not channel_id:
            notify.send_log(f":warning: Kickoff `{k.koid}` — no internal channel for consultant reminder")
            continue
        with notify.best_effort("consultant reminder", k.koid):
            company = flatten_groups(k.data).get("company_name") or k.koid
            who = notify.mention(recipients.paaras_slack_id)
            text = (f"{who} Reminder: a consultant still needs to be assigned for *{company}* "
                    f"(kickoff {k.kickoff_date}).")
            slack_client.post_message(channel_id, text)
            kickoff_repo.merge_reminders(k.koid, {_CONSULTANT: today_iso})
            logger.info("[kickstart] consultant reminder sent for %s", k.koid)
