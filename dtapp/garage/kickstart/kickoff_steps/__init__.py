"""Kickoff step orchestration, split by phase:
  - register.py    — run_register_steps (Phase 1: cleanup → Linear → GTM → Slack)
  - completion.py  — post_tenant_created_slack + run_completion_steps (Phase 3: post-tenant)
  - assign.py      — run_assign_steps + assignment_emails (Phase 4: post-assign-consultant)
  - _runner.py     — the light step-runner (done-flag skip / mark-done / dry-run / error surfacing)
  - common.py      — cross-phase helpers (_kickoff_detail_url, channel-id resolve, bookmarks)

Re-exports the public surface the services import."""

from dtapp.garage.kickstart.kickoff_steps._runner import step_done
from dtapp.garage.kickstart.kickoff_steps.common import _kickoff_detail_url
from dtapp.garage.kickstart.kickoff_steps.register import run_register_steps
from dtapp.garage.kickstart.kickoff_steps.completion import (
    post_tenant_created_slack,
    run_completion_steps,
    update_linear_tid,
)
from dtapp.garage.kickstart.kickoff_steps.assign import (
    assignment_emails,
    run_assign_steps,
)

__all__ = [
    "step_done",
    "_kickoff_detail_url",
    "run_register_steps",
    "post_tenant_created_slack",
    "run_completion_steps",
    "update_linear_tid",
    "assignment_emails",
    "run_assign_steps",
]
