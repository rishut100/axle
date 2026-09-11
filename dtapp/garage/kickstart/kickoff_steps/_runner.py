"""Light step-runner: the repeated scaffolding each kickoff step re-implements — done-flag skip,
mark-done on success, error surfacing on failure, dry-run gating — factored into one place so the
step bodies stay focused on their actual side-effect.

Behaviour is IDENTICAL to the hand-rolled scaffolding the steps used before:
  - step_done skip:   `if step_done(data, flag): return <skip-msg>`  (redelivery / re-drive no-op)
  - dry-run gating:   `if dryrun_unconfigured and not <api key>: dryrun(...); mark done; return`
  - success:          run the body, then `mark_step_done(koid, flag)` LAST (so the side-effect +
                      the checkpoint commit together — a redelivery skips finished work)
  - failure:          best_effort steps surface the cause via record_task_error (task shows
                      "failed") and stay un-done so a re-drive retries; otherwise the body's
                      exception propagates unchanged (the async handler records last_error + lets
                      SQS redeliver), and the step is left un-done so the retry resumes.
"""

import logging

# best_effort (log + Kickstart Logs, never raises) is the shared guard; re-exported here so the step
# files keep importing it from _runner. run_step is the catalog-tracked variant below.
from dtapp.garage.kickstart.notify import best_effort, send_log
from dtapp.garage.kickstart.repositories import kickoff_repo

logger = logging.getLogger(__name__)


def step_done(data, key) -> bool:
    """Completion-step checkpoint (boolean-flag steps): True if `key` is already marked done
    in data['completed_steps'] — so an SQS redelivery skips it (no duplicate side-effect)."""
    return bool((data.get("completed_steps") or {}).get(key))


def dryrun(step, payload):
    logger.info("[kickstart] DRYRUN %s — would send: %s", step, payload)
    return f"dryrun: {step} (service unconfigured)"


def run_step(koid, data, flag, body, *, skip_result=None,
             dryrun_when=False, dryrun_result=None,
             best_effort=False, task_key=None, stage="provisioning"):
    """Wrap a single boolean-flag step with the standard scaffolding; return the body's result.

    flag           completed_steps key — skip (return skip_result) if already done; marked done on
                   success and on the dry-run branch.
    body           callable() -> result; the actual side-effect. Run only when not skipped/dry-run.
    skip_result    value returned when the flag is already done (the per-step "skipped: …" marker).
    dryrun_when    True → skip the body, mark done, return dryrun_result. This is the caller's
                   `settings.dryrun_unconfigured and not <api key>` guard, evaluated by the caller.
    dryrun_result  value returned on the dry-run branch (caller builds it via dryrun(...)).
    best_effort    True → swallow a body exception, record_task_error(stage, task_key or flag, …) so
                   the failure is observable (task shows "failed"), and return a "failed: …" marker
                   WITHOUT marking done (so a re-drive retries). False → the body's exception
                   propagates unchanged and the step is left un-done (the async handler records
                   last_error).
    task_key       record_task_error key when it differs from `flag` (defaults to flag).
    stage          record_task_error stage label (best_effort path only).
    """
    if step_done(data, flag):
        return skip_result
    if dryrun_when:
        kickoff_repo.mark_step_done(koid, flag)
        return dryrun_result
    try:
        result = body()
    except Exception as e:  # noqa: BLE001 — surfaced per best_effort below
        if best_effort:
            logger.warning("[kickstart] %s failed for %s: %s", flag, koid, e)
            kickoff_repo.record_task_error(koid, stage, task_key or flag, str(e))
            send_log(f":warning: Kickoff `{koid}` — {stage}:{task_key or flag} failed: {e}")
            return f"failed: {e}"
        raise
    kickoff_repo.mark_step_done(koid, flag)
    return result
