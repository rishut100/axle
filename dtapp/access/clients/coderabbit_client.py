"""CodeRabbit — User Management REST API (released Jan 2026). Enterprise plan + admin API key.
NOTE: exact endpoint paths below are a best-effort reconstruction from CodeRabbit's public
announcement, not verified against a live account — confirm against current CodeRabbit API docs
before relying on this in prod."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class CodeRabbitClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "CodeRabbit", base_url="https://api.coderabbit.ai/api/v1",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.coderabbit_api_key}"},
        )

    def _configured(self) -> bool:
        return bool(settings.coderabbit_api_key)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN coderabbit %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("CodeRabbit: coderabbit_api_key not configured")

    def invite_member(self, email: str) -> dict:
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post("/users", json={"emails": [email]})

    def remove_member(self, email: str) -> None:
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        self._client.post("/users/remove", json={"emails": [email]})

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        try:
            users = self._client.get("/users", params={"email": email})
            items = users.get("items") or users.get("users") or []
            return "active" if items else "removed"
        except ServiceError:
            return "unknown"


coderabbit_client = CodeRabbitClient()
