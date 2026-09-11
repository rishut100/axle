"""Plum (health insurance) — Partner API. Reclassified from manual after re-checking: Plum's Partner
API (partners.plumhq.com) genuinely supports add/remove members + dependents, not just self-serve UI.
NOTE: exact endpoint paths are a best-effort reconstruction from Plum's public partner-API landing
page, not verified against a live account — confirm against partners.plumhq.com's actual reference
before relying on this in prod. Also note (per Plum's own pricing docs): adding/removing a member off-
cycle is pro-rated against the policy premium — a real cost event, not a no-op API call."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class PlumClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Plum", base_url="https://partners.plumhq.com/api/v1",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.plum_partner_api_key}"},
        )

    def _configured(self) -> bool:
        return bool(settings.plum_partner_api_key and settings.plum_company_id)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN plum %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Plum: plum_partner_api_key/plum_company_id not configured")

    def invite_member(self, email: str, name: str = "") -> dict:
        """POST /companies/{id}/members — real cost event (pro-rated premium), not a free no-op."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post(f"/companies/{settings.plum_company_id}/members",
                                 json={"email": email, "name": name or email.split("@")[0]})

    def remove_member(self, email: str) -> None:
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        member = self._find_by_email(email)
        if member:
            self._client.delete(f"/companies/{settings.plum_company_id}/members/{member['id']}")

    def _find_by_email(self, email: str):
        try:
            resp = self._client.get(f"/companies/{settings.plum_company_id}/members", params={"email": email})
            items = resp.get("data") or resp.get("members") or []
            return items[0] if items else None
        except ServiceError:
            return None

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        return "active" if self._find_by_email(email) else "removed"


plum_client = PlumClient()
