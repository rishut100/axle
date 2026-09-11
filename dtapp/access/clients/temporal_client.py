"""Temporal Cloud — Cloud Ops API (public preview at time of research). The API is primarily gRPC;
this uses its HTTP/JSON transcoding gateway. Auth is a Temporal Cloud API Key (not OAuth).

Path fixed 2026-09-07: the gateway mounts resources directly under /cloud/* — there is NO /api/v1
prefix, despite that being what public docs implied. Confirmed live (real GET https://saas-api.
tmprl.cloud/cloud/users -> 200 with real user data; the old .../api/v1/users path 404s) and against
the real temporalio/cloud-api service.proto, which defines GetUsers/CreateUser/DeleteUser with
google.api.http annotations of GET|POST /cloud/users and DELETE /cloud/users/{user_id}.

DELETE also needs the user's current resourceVersion as a query param (optimistic concurrency —
without it the API returns 400 "resource version mismatch"), confirmed live 2026-09-07 on a real
employee offboard: fetch-then-delete, same pattern GCP IAM already uses for its policy read-modify-
write. DeleteUser is also ASYNC — a 200 here means the operation was accepted, not that it's finished
(returns an asyncOperation id); confirmed by re-listing users a few seconds later and seeing the user
actually gone.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class TemporalClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Temporal", base_url="https://saas-api.tmprl.cloud",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.temporal_cloud_api_key}"},
        )

    def _configured(self) -> bool:
        return bool(settings.temporal_cloud_api_key)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN temporal %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Temporal: temporal_cloud_api_key not configured")

    def invite_member(self, email: str, role: str = "developer") -> dict:
        if self._require_config():
            return self._dryrun("invite_member", {"email": email, "role": role})
        return self._client.post("/cloud/users", json={"spec": {"email": email, "account_access": {"role": role}}})

    def remove_member(self, email: str) -> None:
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        user = self._find_by_email(email)
        if user:
            self._client.delete(f"/cloud/users/{user['id']}",
                                params={"resourceVersion": user["resourceVersion"]})

    def _find_by_email(self, email: str):
        users = self._client.get("/cloud/users")
        for u in users.get("users", []):
            if (u.get("spec", {}).get("email") or "").lower() == email.lower():
                return u
        return None

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        try:
            return "active" if self._find_by_email(email) else "removed"
        except ServiceError:
            return "unknown"


temporal_client = TemporalClient()
