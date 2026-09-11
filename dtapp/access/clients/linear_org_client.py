"""Linear-as-a-grantable-tool (org membership) — DISTINCT from kickstart's use of Linear as this
whole system's ticketing backend (manual-tool sub-issues, drift alerts, etc.). Both share the same
underlying linear_client / LINEAR_API_KEY; this just wraps its invite_org_member/suspend_org_member/
get_org_member_status in the invite_member/remove_member/get_user_status shape.

CORRECTION: originally classified "Linear" as manual (no admin API) based on public-docs research.
That was wrong — confirmed via LIVE introspection of Linear's real GraphQL schema (this repo's own
real LINEAR_API_KEY) that organizationInviteCreate/userSuspend genuinely exist, just undocumented in
Linear's public API reference. Reclassified to method=api, client_name=linear_org.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError
from dtapp.garage.kickstart.clients import linear_client

logger = logging.getLogger(__name__)


class LinearOrgClient:
    def _configured(self) -> bool:
        return bool(settings.linear_api_key)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN linear_org %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("LinearOrg: linear_api_key not configured")

    def invite_member(self, email: str, role: str = "user") -> dict:
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return linear_client.invite_org_member(email, role=role)

    def remove_member(self, email: str) -> None:
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        user_id = linear_client.resolve_user_id(email)
        if user_id:
            linear_client.suspend_org_member(user_id)

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        return linear_client.get_org_member_status(email)


linear_org_client = LinearOrgClient()
