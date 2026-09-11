"""GitBook — REST API (not SCIM, that support was removed from their docs). Org-scoped API token."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class GitBookClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "GitBook", base_url="https://api.gitbook.com",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.gitbook_api_token}"},
        )

    def _configured(self) -> bool:
        return bool(settings.gitbook_api_token and settings.gitbook_org_id)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN gitbook %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("GitBook: gitbook_api_token/gitbook_org_id not configured")

    def invite_member(self, email: str, role: str = "read") -> dict:
        """POST /v1/orgs/{id}/invites."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post(f"/v1/orgs/{settings.gitbook_org_id}/invites",
                                 json={"emails": [email], "role": role})

    def remove_member(self, email: str) -> None:
        """DELETE /v1/orgs/{id}/members/{userId} — needs GitBook's internal member id, resolved from
        the member list (no direct email-lookup endpoint documented)."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        members = self._client.get(f"/v1/orgs/{settings.gitbook_org_id}/members")
        for m in members.get("items", []):
            if (m.get("user", {}).get("email") or "").lower() == email.lower():
                self._client.delete(f"/v1/orgs/{settings.gitbook_org_id}/members/{m['id']}")
                return

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        members = self._client.get(f"/v1/orgs/{settings.gitbook_org_id}/members")
        for m in members.get("items", []):
            if (m.get("user", {}).get("email") or "").lower() == email.lower():
                return "active"
        return "removed"


gitbook_client = GitBookClient()
