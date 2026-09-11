"""AccessEvent status state-machine guard — a plain transition table, mirrors
`dtapp/garage/kickstart/status_machine.py`. `_LEGAL[from]` is the set of statuses `from` may move to.
Same-status writes are always allowed (idempotent re-sets) and are not listed here.

  PENDING       -> IN_PROGRESS                 (queue consumer starts working the checklist)
  IN_PROGRESS   -> DONE                         (every checklist entry resolved)
  IN_PROGRESS   -> PARTIAL_FAILED               (consumer finished a pass; something is still open/failed)
  PARTIAL_FAILED -> IN_PROGRESS                 (a retry / manual "mark done" re-drives the row)
  PARTIAL_FAILED -> DONE                        (a retry / manual "mark done" resolves the last entry)
  DONE          -> IN_PROGRESS                  (rare: a later correction re-opens a "done" event)
"""

from dtapp.access.enums import AccessEventStatus

_LEGAL: dict[AccessEventStatus, set[AccessEventStatus]] = {
    AccessEventStatus.PENDING: {AccessEventStatus.IN_PROGRESS},
    AccessEventStatus.IN_PROGRESS: {AccessEventStatus.DONE, AccessEventStatus.PARTIAL_FAILED},
    AccessEventStatus.PARTIAL_FAILED: {AccessEventStatus.IN_PROGRESS, AccessEventStatus.DONE},
    AccessEventStatus.DONE: {AccessEventStatus.IN_PROGRESS},
}


def is_legal_transition(current: AccessEventStatus, target: AccessEventStatus) -> bool:
    """True if current → target is allowed. Same-status is always allowed (idempotent re-set)."""
    if current == target:
        return True
    return target in _LEGAL.get(current, set())
