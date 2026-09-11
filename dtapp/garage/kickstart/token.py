import hashlib
import secrets
from typing import Optional

from dtapp.garage.kickstart.recipients import recipients
from dtapp.garage.kickstart.repositories import token_repo


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def mint_token(koid, email):
    raw = secrets.token_urlsafe(32)
    token_repo.save_token(_hash(raw), koid, email)  # koid is unique → one link per kickoff, never expires
    return raw  # goes in the email link only; never stored raw


def form_url(raw_token):
    return f"{recipients.form_base_url}/intake/{raw_token}"


def token_state(raw) -> tuple:
    """Return (state, koid): state in {'valid','submitted','invalid'}. Links never expire, so the
    only way to lose 'valid' is using it (→ 'submitted') or a bad/unknown token (→ 'invalid')."""
    tok = token_repo.get_token(_hash(raw))
    if tok is None:
        return ("invalid", None)
    if tok.used_at is not None:
        return ("submitted", tok.koid)
    return ("valid", tok.koid)


def intake_state(koid) -> dict:
    """Detail-page intake state for a koid, from its (single) token. Returns {state}: state in
    {'none','valid','submitted'} — drives the AE intake-link panel (Copy while valid)."""
    tok = token_repo.get_latest_token(koid)
    if tok is None:
        return {"state": "none"}
    if tok.used_at is not None:
        return {"state": "submitted"}
    return {"state": "valid"}


def verify_token(raw) -> Optional[str]:
    """Return koid if token is valid (unused), else None. Read-only; for GET validate."""
    token = token_repo.get_valid_token(_hash(raw))
    return token.koid if token else None


def consume_token(raw) -> Optional[str]:
    """Atomic single-use consume: returns koid if this call won the race, else None."""
    return token_repo.consume_token(_hash(raw))
