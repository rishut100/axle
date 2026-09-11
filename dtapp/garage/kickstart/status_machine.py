"""Kickoff status state-machine guard — a plain transition table, not a framework.

`_LEGAL[from]` is the set of statuses that `from` may move to. Same-status writes are
always allowed (idempotent re-sets) and are not listed here. The edges mirror exactly
what the services perform today (verified against every set_status_explicit call site):

  REGISTERING            → AWAITING_CUSTOMER_RESPONSE   (kickoff_service.handle_register)
  AWAITING_CUSTOMER_RESPONSE → PROVISIONING             (intake_service.begin_provisioning)
  PROVISIONING           → AWAITING_CONSULTANT          (completion_service)
  AWAITING_CONSULTANT    → CONSULTANT_ASSIGNED          (assign_service.assign_consultant)
  CONSULTANT_ASSIGNED    → DONE                         (assign_service.handle_assign)
  DONE                   → CONSULTANT_ASSIGNED          (assign_service re-assign after DONE)
  REGISTERING / PROVISIONING / CONSULTANT_ASSIGNED → FAILED   (queue._dead_letter, permanent give-up)
  FAILED → REGISTERING / PROVISIONING / CONSULTANT_ASSIGNED   (recovery re-drive to the failed stage; no endpoint yet)

The DONE → CONSULTANT_ASSIGNED edge is the documented re-assignment path: Paaras can swap
assignees on a finished kickoff, which briefly moves it back through CONSULTANT_ASSIGNED → DONE.
"""

from dtapp.garage.kickstart.enums import KickoffStatus

_LEGAL: dict[KickoffStatus, set[KickoffStatus]] = {
    KickoffStatus.REGISTERING: {KickoffStatus.AWAITING_CUSTOMER_RESPONSE, KickoffStatus.FAILED},
    KickoffStatus.AWAITING_CUSTOMER_RESPONSE: {KickoffStatus.PROVISIONING},
    KickoffStatus.PROVISIONING: {KickoffStatus.AWAITING_CONSULTANT, KickoffStatus.FAILED},
    KickoffStatus.AWAITING_CONSULTANT: {KickoffStatus.CONSULTANT_ASSIGNED},
    KickoffStatus.CONSULTANT_ASSIGNED: {KickoffStatus.DONE, KickoffStatus.FAILED},
    KickoffStatus.DONE: {KickoffStatus.CONSULTANT_ASSIGNED},
    # FAILED is set at dead-letter (work-transient statuses only — NOT awaiting_* / done, so a stale
    # verify dead-letter can't mark a live kickoff failed). Reverse edges make a recovery re-drive
    # (FAILED → the stage it failed in, then re-enqueue) legal.
    KickoffStatus.FAILED: {
        KickoffStatus.REGISTERING, KickoffStatus.PROVISIONING, KickoffStatus.CONSULTANT_ASSIGNED,
    },
}


def is_legal_transition(current: KickoffStatus, target: KickoffStatus) -> bool:
    """True if current → target is allowed. Same-status is always allowed (idempotent re-set)."""
    if current == target:
        return True
    return target in _LEGAL.get(current, set())
