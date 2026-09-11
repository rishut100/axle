"""Env-resolved routing for access-automation side-effects — mirrors kickstart's recipients.py single
switch point. PROD channel ids are NOT hardcoded here (unlike kickstart's Recipients, which hardcodes
real channel/team ids because those channels already existed) — the #access-automation-* Slack channels
this module needs don't exist yet, so PROD values come from settings (env vars), configured once those
channels are created. NON-prod always routes to the existing Shahbaz dev-test channel (same one
kickstart uses), so a staging/dev run never posts anywhere real regardless of settings.
"""
import logging
from dataclasses import dataclass

from dtapp.garage.core.config import settings

logger = logging.getLogger(__name__)

_SHAHBAZ_DEV_TEST_CHANNEL = "C0B99N8AVFZ"  # #shahbaz-dev-test (same non-prod catch-all kickstart uses)

# Fallback Linear team/state for manual-tool sub-issues when a ToolMatrixEntry doesn't specify one —
# reuses kickstart's non-prod "Eng Dev Testing" bucket outside prod so nothing dev-created ever lands
# in a real team's backlog by accident.
_ENG_DEV_TESTING_TEAM = "2ef1b4b0-6c41-4f0e-9832-66b70028d3fe"
_ENG_DEV_TESTING_TODO = "598a4647-2a56-40f2-a7f4-a493c17b5748"


@dataclass(frozen=True)
class Recipients:
    notify_channel: str          # per-event summary posts ("granted 12/14 tools for jane@...")
    logs_channel: str            # step-failure / best-effort alerts (routine, non-urgent)
    drift_alert_channel: str     # offboarding drift escalation — a still-active tool after revoke.
                                  # Kept SEPARATE from logs_channel: this is a security finding
                                  # ("ex-employee still has access"), not routine noise.
    default_linear_team: str     # manual-tool sub-issue team, when the matrix row doesn't specify one
    default_linear_state: str    # manual-tool sub-issue "Todo"-equivalent state


def _prod_channel(name: str, env_value: str) -> str:
    if not env_value:
        logger.error("[access] settings.%s is not configured — prod posts to it will be skipped", name)
    return env_value


if settings.is_prod:
    # Only evaluated (and only warns on a blank var) when actually running in prod — building this
    # unconditionally alongside _NONPROD made the "not configured" warnings fire in dev too, which is
    # wrong (dev never uses these values at all).
    recipients = Recipients(
        notify_channel=_prod_channel("access_notify_channel", settings.access_notify_channel),
        logs_channel=_prod_channel("access_logs_channel", settings.access_logs_channel),
        drift_alert_channel=_prod_channel("access_drift_alert_channel", settings.access_drift_alert_channel),
        default_linear_team=settings.access_default_linear_team or _ENG_DEV_TESTING_TEAM,
        default_linear_state=settings.access_default_linear_state or _ENG_DEV_TESTING_TODO,
    )
else:
    recipients = Recipients(
        notify_channel=_SHAHBAZ_DEV_TEST_CHANNEL,
        logs_channel=_SHAHBAZ_DEV_TEST_CHANNEL,
        drift_alert_channel=_SHAHBAZ_DEV_TEST_CHANNEL,
        default_linear_team=_ENG_DEV_TESTING_TEAM,
        default_linear_state=_ENG_DEV_TESTING_TODO,
    )
