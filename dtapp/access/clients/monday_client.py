"""Monday.com adapter — wraps kickstart's existing monday_client (dtapp/garage/kickstart/clients/
monday_client.py) with the invite_member/remove_member/get_user_status shape this module's
grant.py/revoke.py/verify_service.py expect.

HONEST LIMITATION: Monday's public API has no "invite a brand-new email to the whole account"
endpoint — `users(emails:)` only resolves EXISTING Monday users, and add_users_to_workspace only adds
someone already resolved. For a person who has genuinely never had a Monday.com account before, this
falls through to a manual step (Monday's SCIM, Enterprise-only, is the real fix — see the capability
research doc). This client correctly automates the common case (the person already exists in Monday
from a prior workspace, or self-serve-signed-up already) and returns a clear failure otherwise, rather
than silently pretending to have invited someone it couldn't reach.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.kickstart.clients.monday_client import monday_client as _kickstart_monday

logger = logging.getLogger(__name__)


class MondayAdapter:
    def _configured(self) -> bool:
        return bool(settings.monday_api_key and settings.monday_home_workspace_id)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN monday %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Monday: monday_api_key/monday_home_workspace_id not configured")

    def invite_member(self, email: str) -> dict:
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        user_map = _kickstart_monday.resolve_user_map([email])
        if not user_map:
            raise ServiceError("Monday", f"{email} has no existing Monday.com account — Monday's API "
                                          f"can't invite a brand-new email; needs SCIM (Enterprise) or a manual invite")
        result = _kickstart_monday.add_owners(settings.monday_home_workspace_id, [email], user_map=user_map)
        return {"id": list(user_map.values())[0], "result": result}

    def remove_member(self, email: str) -> None:
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        _kickstart_monday.remove_users(settings.monday_home_workspace_id, [email])

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        user_map = _kickstart_monday.resolve_user_map([email])
        if not user_map:
            return "removed"
        # No direct "is this user a workspace subscriber" query in kickstart's client; a resolved
        # Monday account is the best signal available without adding a new read method there.
        return "active"


monday_adapter = MondayAdapter()
