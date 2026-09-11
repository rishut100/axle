"""Corporate Okta (your-corp.okta.com) — the client behind the "Drivetrain" matrix row. Per this
team's own convention (Slack: "Is Okta and Drivetrain not the same? ... They both same"), granting an
employee "Drivetrain" access means adding them to the corporate Okta group that fronts SSO into the
internal Drivetrain admin tools — this is a real, scriptable Okta Users/Groups API call, not a manual
step. Distinct from `dtapp/views/okta.py`'s OKTA_API_TOKEN, which targets the customer-facing PRODUCT
Okta (per-tenant customer SSO) — a different Okta org entirely; see access/CLAUDE.md decision #1.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class OktaCorpClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "OktaCorp", base_url=settings.okta_corp_base_url,
            headers_fn=lambda: {"Authorization": f"SSWS {settings.okta_corp_api_token}",
                                "Content-Type": "application/json"},
        )

    def _configured(self) -> bool:
        return bool(settings.okta_corp_api_token)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN okta_corp %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("OktaCorp: okta_corp_api_token not configured")

    def find_user_by_email(self, email: str):
        """GET /api/v1/users/{email} — Okta accepts the login/email directly as the user-id path
        segment. Returns the user resource, or None on a 404."""
        if self._require_config():
            return self._dryrun("find_user", {"email": email})
        try:
            return self._client.get(f"/api/v1/users/{email}")
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def create_user(self, email: str, name: str, extra: dict = None) -> dict:
        """POST /api/v1/users?activate=true — creates the Okta user AND (if okta_corp_drivetrain_group_id
        is set) adds them to the Drivetrain-access group in the same call via groupIds."""
        if self._require_config():
            return self._dryrun("create_user", {"email": email, "name": name})
        first, _, last = name.partition(" ")
        payload = {
            "profile": {"firstName": first or name, "lastName": last or "", "email": email, "login": email},
            **(extra or {}),
        }
        if settings.okta_corp_drivetrain_group_id:
            payload["groupIds"] = [settings.okta_corp_drivetrain_group_id]
        return self._client.post("/api/v1/users?activate=true", json=payload)

    def add_to_drivetrain_group(self, user_id: str) -> None:
        """PUT /api/v1/groups/{groupId}/users/{userId} — for an EXISTING Okta user (the common case:
        most employees already have a corporate Okta account from a prior tool grant; this just adds
        the group membership that fronts Drivetrain-admin SSO)."""
        if self._require_config():
            self._dryrun("add_to_group", {"user_id": user_id})
            return
        if not settings.okta_corp_drivetrain_group_id:
            raise ConfigError("OktaCorp: okta_corp_drivetrain_group_id not configured")
        self._client.post(f"/api/v1/groups/{settings.okta_corp_drivetrain_group_id}/users/{user_id}", json={})

    def invite_member(self, email: str) -> dict:
        """Adapter matching the bespoke-client 'invite_member' shape access_steps/grant.py falls back
        to: find-or-create, then ensure Drivetrain-group membership."""
        user = self.find_user_by_email(email)
        if user and not user.get("dryrun"):
            self.add_to_drivetrain_group(user["id"])
            return user
        return self.create_user(email, email.split("@")[0])

    def remove_member(self, email: str) -> None:
        """Two-step offboard, per Okta's own lifecycle (a user must be DEPROVISIONED before DELETE is
        allowed): POST .../lifecycle/deactivate, then DELETE /api/v1/users/{id} to permanently erase
        the account record. remove_member is only ever invoked from the offboarding path
        (access_steps/revoke.py; grant.py only calls invite_member) — confirmed with the user
        2026-09-07 that a real offboard should hard-delete, not just deactivate (deactivate alone
        blocks every SSO-federated app, which is the security-relevant part, but leaves the record
        around; delete is the deliberate, irreversible extra step requested on top of that)."""
        if self._require_config():
            self._dryrun("deactivate_and_delete_user", {"email": email})
            return
        user = self.find_user_by_email(email)
        if not user:
            return  # already gone — idempotent
        if user.get("status") != "DEPROVISIONED":
            self._client.post(f"/api/v1/users/{user['id']}/lifecycle/deactivate", json={})
        self._client.delete(f"/api/v1/users/{user['id']}")

    def get_user_status(self, email: str) -> str:
        """Used by verify_service for both grant- and revoke-side confirmation. A deactivated Okta
        account is 'inactive' outright (every app is blocked, group membership is moot); otherwise
        falls back to Drivetrain-group membership, since that's what actually gates Drivetrain access
        for an account that's still active for other tools. Returns 'active' | 'inactive'."""
        if self._require_config():
            return "active"  # dry-run: assume the (simulated) op succeeded
        user = self.find_user_by_email(email)
        if not user or user.get("status") != "ACTIVE":
            return "inactive"
        groups = self._client.get(f"/api/v1/users/{user['id']}/groups")
        group_ids = {g.get("id") for g in (groups or [])}
        return "active" if settings.okta_corp_drivetrain_group_id in group_ids else "inactive"


okta_corp_client = OktaCorpClient()
