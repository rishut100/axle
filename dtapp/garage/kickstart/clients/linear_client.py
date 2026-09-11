import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient
from dtapp.garage.kickstart.schemas.clients import LinearIssue

logger = logging.getLogger(__name__)

_CREATE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""

_UPDATE = """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}
"""

_USER_BY_EMAIL = """
query UserByEmail($email: String!) {
  users(filter: { email: { eq: $email } }) { nodes { id } }
}
"""

_USER_STATUS_BY_EMAIL = """
query UserStatusByEmail($email: String!) {
  users(filter: { email: { eq: $email } }) { nodes { id active } }
}
"""

_TEAM_STATES = """
query TeamStates($teamId: String!) {
  team(id: $teamId) { states { nodes { id name type } } }
}
"""

_ORG_INVITE_CREATE = """
mutation OrgInviteCreate($input: OrganizationInviteCreateInput!) {
  organizationInviteCreate(input: $input) { success organizationInvite { id } }
}
"""

_USER_SUSPEND = """
mutation UserSuspend($id: String!) {
  userSuspend(id: $id) { success }
}
"""


class LinearClient:
    """Linear GraphQL client. Inject `service_client` (a ServiceClient) for tests."""

    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Linear", base_url="https://api.linear.app",
            headers_fn=lambda: {"Authorization": settings.linear_api_key, "Content-Type": "application/json"},
        )
        self._done_state_cache: dict[str, str] = {}  # team_id -> completed-type state id

    def _graphql(self, query: str, variables: dict, op: str, require_success: bool = True) -> dict:
        """POST a GraphQL op; raise ServiceError on transport/HTTP (via ServiceClient) or, for
        mutations, on a body-level error / non-success (Linear returns 200 with `errors`). Returns
        data[op]. Pass require_success=False for queries (no `success` field on the payload)."""
        if not settings.linear_api_key:
            raise ConfigError("Linear: linear_api_key not configured")
        body = self._client.post("/graphql", json={"query": query, "variables": variables})
        result = (body.get("data") or {}).get(op) if not body.get("errors") else None
        if require_success and (not result or not result.get("success")):
            raise ServiceError("Linear", f"error: {body.get('errors') or body}")
        return result

    def create_issue(self, team_id, title, description=None, assignee_id=None, parent_id=None,
                     priority=None, state_id=None) -> LinearIssue:
        """Create a Linear issue in the given team. Returns a LinearIssue {id, identifier, url}.

        Generic — the kickstart steps compose it: KO org issue (Kickoff team), per-connector
        issues (Connectors team, assignee Praneeth), academy / Heimdall sub-issues (parentId =
        the KO org issue). Raises ServiceError on any non-success (caller decides fail vs best-effort).
        """
        issue_input = {"teamId": team_id, "title": title}
        if description:
            issue_input["description"] = description
        if assignee_id:
            issue_input["assigneeId"] = assignee_id
        if parent_id:
            issue_input["parentId"] = parent_id
        if priority is not None:
            issue_input["priority"] = priority
        if state_id:
            issue_input["stateId"] = state_id
        issue = self._graphql(_CREATE, {"input": issue_input}, "issueCreate")["issue"]  # {id, identifier, url}
        return LinearIssue.model_validate(issue)

    def update_issue(self, issue_id, description=None, assignee_id=None) -> None:
        """Update an existing issue — its description (inject the tenant ID once created) and/or its
        assignee (reassign the 'Update Kickoff Deck' sub-issue to the assigned consultant). No-op if
        neither is given."""
        payload = {}
        if description is not None:
            payload["description"] = description
        if assignee_id is not None:
            payload["assigneeId"] = assignee_id
        if not payload:
            return
        self._graphql(_UPDATE, {"id": issue_id, "input": payload}, "issueUpdate")

    def get_done_state_id(self, team_id: str) -> str:
        """The team's workflow state with type=='completed' (Linear's canonical "Done"-equivalent —
        the actual name varies per team, e.g. "Done" vs "Merged"). Cached per team_id (states are
        stable enough not to re-query every call). Raises ServiceError if the team has no completed
        state (shouldn't happen — every Linear team has one by default)."""
        if team_id in self._done_state_cache:
            return self._done_state_cache[team_id]
        data = self._graphql(_TEAM_STATES, {"teamId": team_id}, "team", require_success=False)
        for state in (data or {}).get("states", {}).get("nodes", []):
            if state.get("type") == "completed":
                self._done_state_cache[team_id] = state["id"]
                return state["id"]
        raise ServiceError("Linear", f"no completed-type state found for team {team_id}")

    def close_issue(self, issue_id: str, team_id: str) -> None:
        """Move an issue to its team's Done-equivalent state — the auto-close half of the manual-tool
        completion flow (grant.py/revoke.py create the ticket; this closes it once a human confirms
        via Slack reaction or the /complete backstop route)."""
        state_id = self.get_done_state_id(team_id)
        self._graphql(_UPDATE, {"id": issue_id, "input": {"stateId": state_id}}, "issueUpdate")

    def invite_org_member(self, email: str, role: str = "user", team_ids: list = None) -> dict:
        """organizationInviteCreate — a REAL Linear org-membership invite (distinct from
        create_issue's ticketing use). Confirmed live against Linear's actual GraphQL schema via
        introspection (this repo's own real LINEAR_API_KEY) rather than assumed from docs — Linear's
        public docs don't surface this mutation, but it exists: email (required), role
        (UserRoleType: admin/user/guest), teamIds (optional scoping)."""
        payload = {"email": email, "role": role}
        if team_ids:
            payload["teamIds"] = team_ids
        result = self._graphql(_ORG_INVITE_CREATE, {"input": payload}, "organizationInviteCreate")
        return result.get("organizationInvite") or {}

    def suspend_org_member(self, user_id: str) -> None:
        """userSuspend — the real offboarding counterpart, confirmed via the same live introspection."""
        self._graphql(_USER_SUSPEND, {"id": user_id}, "userSuspend")

    def get_org_member_status(self, email: str) -> str:
        """True Linear membership status (User.active), not the invite-pending state."""
        if not settings.linear_api_key:
            raise ConfigError("Linear: linear_api_key not configured")
        data = self._graphql(_USER_STATUS_BY_EMAIL, {"email": email.strip().lower()}, "users", require_success=False)
        nodes = (data or {}).get("nodes") or []
        if not nodes:
            return "removed"
        return "active" if nodes[0].get("active") else "inactive"

    def resolve_user_id(self, email):
        """Best-effort Linear user-id for an email — used to assign the intro-email issue to the AE
        (registered_by, dynamic per kickoff). Returns None on no key / not found / any error, and the
        caller then leaves the issue unassigned."""
        if not email or not settings.linear_api_key:
            return None
        try:
            # Linear stores emails lowercased; eq is case-sensitive, so normalize the lookup.
            users = self._graphql(_USER_BY_EMAIL, {"email": email.strip().lower()}, "users", require_success=False)
            nodes = (users or {}).get("nodes") or []
            return nodes[0].get("id") if nodes else None
        except Exception as e:  # noqa: BLE001 — best-effort; assignment is optional
            logger.warning("[kickstart] linear user lookup failed for %s: %s", email, e)
            return None


linear_client = LinearClient()
