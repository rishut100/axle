"""HubSpot — REST API v3 (not SCIM). Private App token."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class HubSpotClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "HubSpot", base_url="https://api.hubapi.com",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.hubspot_api_token}"},
        )

    def _configured(self) -> bool:
        return bool(settings.hubspot_api_token)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN hubspot %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("HubSpot: hubspot_api_token not configured")

    def invite_member(self, email: str) -> dict:
        """POST /settings/v3/users — note: API-created users don't automatically consume a paid seat;
        confirm seat assignment separately if that matters for your plan."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        payload = {"email": email}
        if settings.hubspot_default_role_id:
            payload["roleId"] = settings.hubspot_default_role_id
        return self._client.post("/settings/v3/users", json=payload)

    def remove_member(self, email: str) -> None:
        """DELETE /settings/v3/users/{userId} — HubSpot accepts email OR numeric id as the path id."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        self._client.delete(f"/settings/v3/users/{email}")

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        try:
            self._client.get(f"/settings/v3/users/{email}")
            return "active"
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return "removed"
            raise


hubspot_client = HubSpotClient()
