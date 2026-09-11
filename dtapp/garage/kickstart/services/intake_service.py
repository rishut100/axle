from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart import token
from dtapp.garage.core.errors import ConflictError, NotFoundError
from dtapp.garage.kickstart.sqs import flush_outbox


def begin_provisioning(koid: str, details: dict):
    """Persist intake details, set status PROVISIONING, and record the provision enqueue-intent in ONE
    transaction (kickoff_repo.begin_provisioning_atomic), then publish it. Returns fast — the sync Drive
    REST create runs in Axle's consumer. Body is the koid only; the consumer re-loads the kickoff."""
    if not kickoff_repo.begin_provisioning_atomic(koid, details):
        raise NotFoundError(f"Kickoff {koid} not found")
    flush_outbox(koid=koid)


def submit_intake(raw_token, submission):
    """Race-safe single-submit: atomic token consume then delegate to begin_provisioning."""
    koid = token.consume_token(raw_token)  # atomic single-use; only one concurrent caller wins
    if not koid:
        raise ConflictError("This intake link is invalid or has already been used.")
    begin_provisioning(koid, submission.model_dump(mode="json"))
    return koid
