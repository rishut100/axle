"""Zoom — Admin API via OAuth2 Server-to-Server (JWT apps were deprecated June 2023). Caches the
access token in memory until near expiry, same pattern as microsoft365_client.py."""
import base64
import logging
import time

import requests

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class ZoomClient:
    def __init__(self, service_client=None):
        self._token = None
        self._token_expires_at = 0
        self._client = service_client or ServiceClient(
            "Zoom", base_url="https://api.zoom.us/v2",
            headers_fn=lambda: {"Authorization": f"Bearer {self._get_token()}"},
        )

    def _configured(self) -> bool:
        return bool(settings.zoom_account_id and settings.zoom_client_id and settings.zoom_client_secret)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN zoom %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Zoom: zoom_account_id/client_id/client_secret not configured")

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        basic = base64.b64encode(f"{settings.zoom_client_id}:{settings.zoom_client_secret}".encode()).decode()
        resp = requests.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": settings.zoom_account_id},
            headers={"Authorization": f"Basic {basic}"}, timeout=15,
        )
        if resp.status_code >= 300:
            raise ServiceError("Zoom", f"token exchange failed: HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + body.get("expires_in", 3600)
        return self._token

    def invite_member(self, email: str, user_type: int = 1) -> dict:
        """POST /users — user_type 1=Basic, 2=Licensed, 3=On-prem."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post("/users", json={
            "action": "create", "user_info": {"email": email, "type": user_type},
        })

    def remove_member(self, email: str) -> None:
        """PUT /users/{id}/status action=deactivate — note: doesn't free a paid license seat; a real
        offboarding flow may also need DELETE for that, deliberately not done here (deactivate is the
        reversible default; escalate to a human for the license-reclaim decision)."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        self._client.put(f"/users/{email}/status", json={"action": "deactivate"})

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "inactive"
        try:
            user = self._client.get(f"/users/{email}")
            return user.get("status", "active")
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return "removed"
            raise


zoom_client = ZoomClient()
