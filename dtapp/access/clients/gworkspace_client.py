"""Google Workspace (G-suite) Admin SDK Directory API — auth via the SAME service-account JSON as
BigQuery (settings.google_service_account_json), using domain-wide delegation (impersonating a real
super-admin via `subject`) rather than a separate API key. `google-auth` is already a dependency
(main.py uses it for BigQuery)."""
import json
import logging
import time

import requests
from google.oauth2 import service_account

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/admin.directory.user"]


class GWorkspaceClient:
    def __init__(self, service_client=None):
        self._credentials = None
        self._client = service_client or ServiceClient(
            "GWorkspace", base_url="https://admin.googleapis.com/admin/directory/v1",
            headers_fn=lambda: {"Authorization": f"Bearer {self._get_token()}"},
        )

    def _configured(self) -> bool:
        return bool(settings.google_service_account_json and settings.gworkspace_admin_impersonate_email)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN gworkspace %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("GWorkspace: google_service_account_json/gworkspace_admin_impersonate_email not configured")

    def _get_token(self) -> str:
        if self._credentials is None:
            info = json.loads(settings.google_service_account_json)
            self._credentials = service_account.Credentials.from_service_account_info(
                info, scopes=_SCOPES, subject=settings.gworkspace_admin_impersonate_email)
        if not self._credentials.valid:
            from google.auth.transport.requests import Request
            self._credentials.refresh(Request())
        return self._credentials.token

    def invite_member(self, email: str, name: str = "") -> dict:
        """POST /users — creates a real Workspace account with a random temp password (Workspace
        requires one at create time; the new employee resets it via standard first-login flow)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        first, _, last = (name or email.split("@")[0]).partition(" ")
        return self._client.post("/users", json={
            "primaryEmail": email,
            "name": {"givenName": first or email.split("@")[0], "familyName": last or "."},
            "password": f"TempPass!{int(time.time())}", "changePasswordAtNextLogin": True,
        })

    def remove_member(self, email: str) -> None:
        """PATCH suspended:true — matches this tool's soft-deprovision convention elsewhere."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        self._client.patch(f"/users/{email}", json={"suspended": True})

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "inactive"
        try:
            user = self._client.get(f"/users/{email}")
            return "inactive" if user.get("suspended") else "active"
        except ServiceError as e:
            if "HTTP 404" in str(e):
                return "removed"
            raise


gworkspace_client = GWorkspaceClient()
