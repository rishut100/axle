"""PostHog — REST API (not SCIM). Org-scoped personal API key + org id."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class PostHogClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "PostHog", base_url=settings.posthog_base_url,
            headers_fn=lambda: {"Authorization": f"Bearer {settings.posthog_api_key}"},
        )

    def _configured(self) -> bool:
        return bool(settings.posthog_api_key and settings.posthog_org_id)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN posthog %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("PostHog: posthog_api_key/posthog_org_id not configured")

    def invite_member(self, email: str) -> dict:
        """POST /api/organizations/{id}/invites/ — creates a pending invite (not an immediate member;
        PostHog requires the invitee to accept)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        return self._client.post(f"/api/organizations/{settings.posthog_org_id}/invites/",
                                 json={"target_email": email})

    def remove_member(self, email: str) -> None:
        """DELETE /api/organizations/{id}/members/{user__uuid}/ — the path param is the USER's uuid
        (m['user']['uuid']), NOT the membership record's own 'id' field. Confirmed real bug 2026-09-07:
        the membership id LOOKS like a valid uuid too, so passing it silently 404s instead of erroring
        obviously — verified against PostHog's own API docs after a real offboard hit this."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        members = self._client.get(f"/api/organizations/{settings.posthog_org_id}/members/")
        for m in members.get("results", []):
            if (m.get("user", {}).get("email") or "").lower() == email.lower():
                user_uuid = m["user"]["uuid"]
                self._client.delete(f"/api/organizations/{settings.posthog_org_id}/members/{user_uuid}/")
                return

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        members = self._client.get(f"/api/organizations/{settings.posthog_org_id}/members/")
        for m in members.get("results", []):
            if (m.get("user", {}).get("email") or "").lower() == email.lower():
                return "active"
        return "removed"


posthog_client = PostHogClient()
