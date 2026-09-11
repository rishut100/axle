"""Generic SCIM 2.0 client — covers the ~15 tools whose admin API is standard SCIM (Notion, Retool,
Postman, LaunchDarkly, 1Password, Slack Enterprise Grid, Figma, Cursor, GitHub Enterprise, dbt Cloud,
Fivetran, ...): base_url + bearer token + resource schema, all supplied per-tool via
ToolMatrixEntry.client_config, rather than one bespoke class per tool (decision #5). A bespoke client
is only worth writing for a genuine outlier whose lifecycle API ISN'T SCIM-shaped (AWS IAM, Google
Workspace Admin SDK, GCP IAM, GitHub's plain REST org-membership API) — see clients/github_client.py
for that shape.

Same conventions as kickstart's clients (linear_client.py, slack_client.py): wraps one ServiceClient,
raises ConfigError on missing config, ServiceError on transport/HTTP failure, dry-run-when-unconfigured
in non-prod via settings.dryrun_unconfigured.
"""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class ScimClient:
    def __init__(self, tool_name: str, base_url: str, token: str, service_client=None):
        self._tool = tool_name
        self._base_url = base_url
        self._token = token
        self._client = service_client or ServiceClient(
            f"SCIM:{tool_name}", base_url=base_url,
            headers_fn=lambda: {"Authorization": f"Bearer {token}", "Content-Type": "application/scim+json"},
        )

    def _configured(self) -> bool:
        return bool(self._base_url and self._token)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN scim:%s %s — would send: %s", self._tool, op, payload)
        return {"dryrun": True, "tool": self._tool, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError(f"SCIM:{self._tool} — base_url/token not configured")

    def find_user_by_email(self, email: str):
        """GET /Users?filter=userName eq "..." (SCIM standard filter syntax). Returns the first match's
        SCIM resource dict, or None if not found. `userName` is the SCIM-standard filter attribute;
        a handful of vendors expect `emails.value` instead — override via client_config["filter_attr"]
        at the matrix-row level if a tool needs it (not needed for the tools configured so far)."""
        if self._require_config():
            return self._dryrun("find_user", {"email": email})
        body = self._client.get("/Users", params={"filter": f'userName eq "{email}"'})
        resources = body.get("Resources") or []
        return resources[0] if resources else None

    def create_user(self, email: str, name: str, extra: dict = None) -> dict:
        """POST /Users. `extra` merges into the SCIM resource body for tool-specific attributes (e.g. a
        role/group id) — supplied via ToolMatrixEntry.client_config["create_extra"]."""
        if self._require_config():
            return self._dryrun("create_user", {"email": email, "name": name})
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": email,
            "emails": [{"value": email, "primary": True}],
            "name": {"formatted": name},
            "active": True,
            **(extra or {}),
        }
        return self._client.post("/Users", json=payload)

    def deactivate_user(self, scim_user_id: str) -> None:
        """PATCH active=false — the SCIM-standard deprovision op (soft-deactivate, not a hard delete;
        matches how most of these vendors actually behave even on a DELETE call)."""
        if self._require_config():
            self._dryrun("deactivate_user", {"scim_user_id": scim_user_id})
            return
        payload = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "value": {"active": False}}],
        }
        self._client.patch("/Users/" + scim_user_id, json=payload)

    def get_user_status(self, scim_user_id: str) -> str:
        """GET /Users/<id> -> 'active' | 'inactive'. Used by the offboarding-verify cron to confirm a
        prior deactivate_user call actually took (decision #7)."""
        if self._require_config():
            return "inactive"  # dry-run: assume the (simulated) deactivate succeeded
        body = self._client.get(f"/Users/{scim_user_id}")
        return "active" if body.get("active") else "inactive"


def make_scim_client(tool_name: str, client_config: dict) -> ScimClient:
    """Factory used by access_steps.common.CLIENT_REGISTRY — builds a ScimClient from a
    ToolMatrixEntry.client_config dict: {"base_url": "...", "token_env": "NOTION_SCIM_TOKEN"} or
    {"base_url": "...", "token": "..."} (env-var indirection preferred so tokens aren't duplicated
    across matrix rows; see the plaintext-JSONB caveat in matrix_repo.py's module docstring)."""
    base_url = client_config.get("base_url", "")
    token = client_config.get("token", "")
    if not token and client_config.get("token_env"):
        import os
        token = os.environ.get(client_config["token_env"], "")
    return ScimClient(tool_name, base_url, token)
