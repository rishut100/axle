"""Clockify — REST API. Workspace-scoped admin/owner API key."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class ClockifyClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Clockify", base_url="https://api.clockify.me/api/v1",
            headers_fn=lambda: {"X-Api-Key": settings.clockify_api_key},
        )

    def _configured(self) -> bool:
        return bool(settings.clockify_api_key and settings.clockify_workspace_id)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN clockify %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Clockify: clockify_api_key/clockify_workspace_id not configured")

    def invite_member(self, email: str) -> dict:
        """POST /workspaces/{id}/users — invites by email."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post(f"/workspaces/{settings.clockify_workspace_id}/users",
                                 json={"emails": [email]})

    def remove_member(self, email: str) -> None:
        """PUT /workspaces/{id}/users/{userId} status=INACTIVE — Clockify's DELETE endpoint is
        deprecated; deactivating (not removing) is the supported path. Needs the user id, resolved
        from the workspace user list."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        users = self._client.get(f"/workspaces/{settings.clockify_workspace_id}/users")
        for u in users:
            if (u.get("email") or "").lower() == email.lower():
                self._client.put(f"/workspaces/{settings.clockify_workspace_id}/users/{u['id']}",
                                 json={"status": "INACTIVE"})
                return

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "inactive"
        users = self._client.get(f"/workspaces/{settings.clockify_workspace_id}/users")
        for u in users:
            if (u.get("email") or "").lower() == email.lower():
                return "active" if u.get("status") == "ACTIVE" else "inactive"
        return "inactive"


clockify_client = ClockifyClient()
