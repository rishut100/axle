"""Idempotent, best-effort step wrapper — copied and adapted from
dtapp/garage/kickstart/kickoff_steps/_runner.py (that one is hard-wired to kickoff_repo; this is the
same scaffolding wired to access_event_repo instead). One vendor's API failure must never strand the
other N tools in the same event's checklist — see the plan's decision #1 rationale.
"""
import logging

from dtapp.access.notify import best_effort, send_log  # noqa: F401 — re-exported for step files
from dtapp.access.repositories import access_event_repo

logger = logging.getLogger(__name__)


def step_done(data, key) -> bool:
    """True if `key` is already marked done in completed_steps — an SQS redelivery skips it."""
    return bool((data.get("completed_steps") or {}).get(key))


def dryrun(step, payload):
    logger.info("[access] DRYRUN %s — would send: %s", step, payload)
    return f"dryrun: {step} (service unconfigured)"


def run_step(aeid, data, flag, body, *, skip_result=None,
             best_effort=False, task_key=None, stage="dispatch"):
    """Wrap one boolean-flag step: skip if already done, run `body()`, mark done on success. On
    failure: best_effort=True swallows the exception, records a task_error (checklist entry shows
    'failed', event rolls up to PARTIAL_FAILED, but every OTHER tool's step still runs) and leaves the
    step un-done so a redelivery/retry can re-attempt it; best_effort=False lets the exception
    propagate (caller decides — used only for the checklist-build step itself, never per-tool)."""
    if step_done(data, flag):
        return skip_result
    try:
        result = body()
    except Exception as e:  # noqa: BLE001 — surfaced per best_effort below
        if best_effort:
            logger.warning("[access] %s failed for %s: %s", flag, aeid, e)
            access_event_repo.record_task_error(aeid, stage, task_key or flag, str(e))
            send_log(f":warning: AccessEvent `{aeid}` — {stage}:{task_key or flag} failed: {e}")
            return f"failed: {e}"
        raise
    access_event_repo.mark_step_done(aeid, flag)
    return result
