"""Keka HRMS — REST API via OAuth2 (client_id + client_secret + api_key, scope 'kekaapi'). Reclassified
from manual after re-checking: Keka's API does support employee create + exit-request management, not
just read access. NOTE: exact endpoint paths below are a best-effort reconstruction from Keka's public
developer docs, not verified against a live account — confirm against developers.keka.com before
relying on this in prod. Distinct from KEKA_API_KEY/keka_api_base_url (settings), which back the
*inbound* webhook trigger's optional outbound lookups — this is Keka as a GRANT TARGET (the employee's
own Keka login), using the separate OAuth client credentials."""
import logging
import time

import requests

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class KekaClient:
    def __init__(self, service_client=None):
        self._token = None
        self._token_expires_at = 0
        self._client = service_client or ServiceClient(
            "Keka", base_url=settings.keka_api_base_url,
            headers_fn=lambda: {"Authorization": f"Bearer {self._get_token()}"},
        )

    def _configured(self) -> bool:
        return bool(settings.keka_oauth_client_id and settings.keka_oauth_client_secret and settings.keka_api_key)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN keka %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Keka: keka_oauth_client_id/client_secret/keka_api_key not configured")

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        resp = requests.post(
            f"{settings.keka_api_base_url}/connect/token",
            data={"grant_type": "kekaapi", "scope": "kekaapi", "client_id": settings.keka_oauth_client_id,
                 "client_secret": settings.keka_oauth_client_secret, "api_key": settings.keka_api_key},
            timeout=15,
        )
        if resp.status_code >= 300:
            raise ServiceError("Keka", f"token exchange failed: HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + body.get("expires_in", 3600)
        return self._token

    def invite_member(self, email: str, name: str = "") -> dict:
        """POST /hris/employees — creates a new employee record (which is also their Keka login)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        first, _, last = (name or email.split("@")[0]).partition(" ")
        return self._client.post("/hris/employees", json={
            "email": email, "firstName": first or email.split("@")[0], "lastName": last or ".",
        })

    def remove_member(self, email: str) -> None:
        """Keka models offboarding as an exit request, not a hard delete — POST an exit request for
        the employee rather than deleting the HR record."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        employee = self._find_by_email(email)
        if employee:
            self._client.post(f"/hris/employees/{employee['id']}/exit", json={"type": "resignation"})

    def _find_by_email(self, email: str):
        try:
            resp = self._client.get("/hris/employees", params={"email": email})
            items = resp.get("data") or resp.get("employees") or []
            return items[0] if items else None
        except ServiceError:
            return None

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        employee = self._find_by_email(email)
        if not employee:
            return "removed"
        return "active" if employee.get("status", "active").lower() == "active" else "inactive"


keka_client = KekaClient()
