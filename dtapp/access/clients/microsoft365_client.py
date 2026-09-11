"""Microsoft 365 / Entra ID — Graph API via OAuth2 client-credentials (not a static bearer token like
most other clients here). Caches the access token in memory until near expiry."""
import logging
import time

import requests

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class Microsoft365Client:
    def __init__(self, service_client=None):
        self._token = None
        self._token_expires_at = 0
        self._client = service_client or ServiceClient(
            "Microsoft365", base_url="https://graph.microsoft.com/v1.0",
            headers_fn=lambda: {"Authorization": f"Bearer {self._get_token()}"},
        )

    def _configured(self) -> bool:
        return bool(settings.ms_graph_tenant_id and settings.ms_graph_client_id and settings.ms_graph_client_secret)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN microsoft365 %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Microsoft365: ms_graph_tenant_id/client_id/client_secret not configured")

    def _get_token(self) -> str:
        """OAuth2 client-credentials exchange, cached until ~60s before expiry. Only called when
        actually configured (dry-run short-circuits before any client method reaches here)."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        resp = requests.post(
            f"https://login.microsoftonline.com/{settings.ms_graph_tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": settings.ms_graph_client_id,
                 "client_secret": settings.ms_graph_client_secret, "scope": "https://graph.microsoft.com/.default"},
            timeout=15,
        )
        if resp.status_code >= 300:
            raise ServiceError("Microsoft365", f"token exchange failed: HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + body.get("expires_in", 3600)
        return self._token

    def invite_member(self, email: str) -> dict:
        """POST /users — creates a real M365 account (needs a temp password + license assignment
        separately via /users/{id}/assignLicense, not included here)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        name = email.split("@")[0]
        return self._client.post("/users", json={
            "accountEnabled": True, "displayName": name, "mailNickname": name,
            "userPrincipalName": email,
            "passwordProfile": {"forceChangePasswordNextSignIn": True, "password": "TempPass!" + str(int(time.time()))},
        })

    def remove_member(self, email: str) -> None:
        """PATCH accountEnabled:false (disable, not delete — matches this tool's convention of
        soft-deprovisioning elsewhere)."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        self._client.patch(f"/users/{email}", json={"accountEnabled": False})

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "inactive"
        try:
            user = self._client.get(f"/users/{email}", params={"$select": "accountEnabled"})
            return "active" if user.get("accountEnabled") else "inactive"
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return "removed"
            raise


microsoft365_client = Microsoft365Client()
