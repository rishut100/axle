"""GitHub org membership — a bespoke client (not SCIM; GitHub's org-membership REST API has its own
shape) demonstrating the pattern non-SCIM tools follow. Same conventions as kickstart's linear_client.py.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "GitHub", base_url="https://api.github.com",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.github_api_token}",
                                "Accept": "application/vnd.github+json"},
        )
        self._member_logins = None  # cached org member list — see _list_org_members

    def _configured(self) -> bool:
        return bool(settings.github_api_token)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN github %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("GitHub: github_api_token not configured")

    def invite_member(self, email: str, role: str = "member") -> dict:
        """PUT /orgs/{org}/memberships/{username} needs a username, not an email — GitHub has no
        email-based org invite on this endpoint; use POST /orgs/{org}/invitations (email-based) instead."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email, "role": role})
        return self._client.post(f"/orgs/{settings.github_org}/invitations",
                                 json={"email": email, "role": role})

    def _list_org_members(self) -> list:
        """GET /orgs/{org}/members (paginated) — cached per client instance for the process lifetime.
        Drivetrain's GitHub org is Team-plan, not Enterprise Cloud, so there's no SAML/SCIM identity
        API to map an email to a login directly (confirmed live: /credential-authorizations returns
        empty on this org) — the real member list is the only reliable source of truth."""
        if self._member_logins is not None:
            return self._member_logins
        logins, page = [], 1
        while True:
            batch = self._client.get(f"/orgs/{settings.github_org}/members",
                                     params={"per_page": 100, "page": page})
            if not batch:
                break
            logins.extend(m["login"] for m in batch)
            if len(batch) < 100:
                break
            page += 1
        self._member_logins = logins
        return logins

    def _find_username(self, email: str) -> str:
        """Resolve email -> real GitHub login by matching against the actual org member list, instead
        of guessing (an earlier version just used the email local-part verbatim, which is WRONG — this
        org's members are `<name>-dt`, e.g. shaswat@drivetrain.ai -> shaswat-dt, not `shaswat`; that
        guess caused a real 404 on a live offboard). Exact match first, then a unique `<local>-*` login
        (the observed company convention); raises loudly on no/ambiguous match rather than silently
        guessing wrong again — a failed step is safer than a wrong one for a revoke."""
        local = email.split("@")[0].lower()
        logins = self._list_org_members()
        exact = [l for l in logins if l.lower() == local]
        if exact:
            return exact[0]
        prefixed = [l for l in logins if l.lower().startswith(local + "-")]
        if len(prefixed) == 1:
            return prefixed[0]
        if len(prefixed) > 1:
            raise ServiceError("GitHub", f"ambiguous GitHub username match for {email}: {prefixed}")
        raise ServiceError("GitHub", f"no GitHub org member found matching {email} (checked {len(logins)} members)")

    def remove_member(self, email: str) -> None:
        """DELETE /orgs/{org}/members/{username} — immediate removal (GitHub org membership has no
        separate 'deactivate' state; removal IS the revoke). Takes an email like every other bespoke
        client's public method — resolves the real username internally via _find_username."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        username = self._find_username(email)
        self._client.delete(f"/orgs/{settings.github_org}/members/{username}")

    def get_user_status(self, email: str) -> str:
        """GET /orgs/{org}/memberships/{username} -> 'active' | 'pending', or 'removed' (404 on the
        membership call, OR the email simply doesn't resolve to any current member — both mean gone).
        Used by both the grant- and revoke-side verify crons."""
        if self._require_config():
            return "removed"  # dry-run: assume the (simulated) removal succeeded
        try:
            username = self._find_username(email)
        except ServiceError:
            return "removed"  # no matching member at all -> already gone
        try:
            body = self._client.get(f"/orgs/{settings.github_org}/memberships/{username}")
            return body.get("state", "active")
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return "removed"
            raise


github_client = GitHubClient()
