import logging

from dtapp.garage.kickstart.clients import gsheet_client
from dtapp.garage.kickstart.repositories import consultant_repo

logger = logging.getLogger(__name__)


def sync_consultants() -> dict:
    """Pull the Customer-Success consultant roster from the Google Sheet and reconcile.

    Upserts every current CS person (department == "Customer Success"); the upsert
    reactivates returning people and keeps their name current. Then soft-deletes (NOT
    hard-deletes) every consultant whose email is no longer in the roster — they've left.
    Idempotent — safe to run repeatedly or double-fire under HA.
    Returns {"synced": <count>, "deactivated": <count>, "source": "gsheet"}.
    """
    rows = gsheet_client.read_consultant_rows()
    synced = 0
    roster_emails: set[str] = set()
    for row in rows:
        email = row.email
        if not email:
            continue
        if row.department.strip().lower() != "customer success":
            continue
        name = row.name or email
        consultant_repo.upsert_consultant(name, email)
        roster_emails.add(email.strip().lower())
        synced += 1
    # Empty roster ⇒ sheet read failed/blank creds (read_consultant_rows is fail-soft).
    # A real CS roster is never 0 people, so skip the reconcile rather than wipe everyone.
    if not roster_emails:
        logger.warning("[kickstart] consultant sync: empty roster — skipping reconcile (no soft-deletes)")
        return {"synced": 0, "deactivated": 0,
                "skipped": "empty roster — sheet read returned no rows", "source": "gsheet"}
    # Reconcile: soft-delete anyone active in the DB but absent from the current roster.
    deactivated = 0
    for c in consultant_repo.list_consultants():  # active only
        if c.email.strip().lower() not in roster_emails:
            if consultant_repo.deactivate_consultant(consultant_id=c.id):
                deactivated += 1
    logger.info("[kickstart] consultant sync: %d synced (active CS), %d deactivated (left) from gsheet", synced, deactivated)
    return {"synced": synced, "deactivated": deactivated, "source": "gsheet"}
