"""Instantly.ai — full REST API v2, Workspace Member resource. Scoped API key."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class InstantlyClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Instantly", base_url="https://api.instantly.ai/api/v2",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.instantly_api_key}"},
        )

    def _configured(self) -> bool:
        return bool(settings.instantly_api_key)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN instantly %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Instantly: instantly_api_key not configured")

    def invite_member(self, email: str, role: str = "member") -> dict:
        """POST /workspace-members — 'owner' role can't be created via API (Instantly restriction)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email, "role": role})
        return self._client.post("/workspace-members", json={"email": email, "role": role})

    def remove_member(self, email: str) -> None:
        """DELETE /workspace-members/{id} — needs Instantly's member id, resolved from the list."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        members = self._client.get("/workspace-members")
        for m in members.get("items", members if isinstance(members, list) else []):
            if (m.get("email") or "").lower() == email.lower():
                self._client.delete(f"/workspace-members/{m['id']}")
                return

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        members = self._client.get("/workspace-members")
        for m in members.get("items", members if isinstance(members, list) else []):
            if (m.get("email") or "").lower() == email.lower():
                return "active"
        return "removed"


instantly_client = InstantlyClient()
