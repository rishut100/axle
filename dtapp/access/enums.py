from enum import Enum


class AccessEventType(str, Enum):
    ONBOARD = "onboard"
    OFFBOARD = "offboard"


class AccessEventStatus(str, Enum):
    # PENDING: row exists, checklist built, outbox row written — the async grant/revoke consumer
    # hasn't run yet.
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    # DONE: every checklist entry resolved (api → granted/revoked, manual → done).
    DONE = "done"
    # PARTIAL_FAILED: the consumer ran, but at least one checklist entry is in a terminal failure/open
    # state (api call failed after retries, or a manual ticket is still open). Not a dead end — a
    # retry/manual "mark done" can still move entries to DONE.
    PARTIAL_FAILED = "partial_failed"


class ToolMethod(str, Enum):
    API = "api"
    MANUAL = "manual"


class ToolGrantStatus(str, Enum):
    """Per-tool checklist entry status (AccessEvent.checklist[tool]['status'])."""
    PENDING = "pending"
    # api-method terminal states
    GRANTED = "granted"
    REVOKED = "revoked"
    FAILED = "failed"
    # manual-method states
    TICKET_OPEN = "ticket_open"
    DONE = "done"


class VerifyResult(str, Enum):
    CONFIRMED = "confirmed"   # re-check agrees with the expected post-revoke state
    DRIFT = "drift"           # re-check shows the tool still thinks the user is active
    UNKNOWN = "unknown"       # re-check itself failed (transport/API error) — neither confirmed nor drift
