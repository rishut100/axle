import logging

from dtapp.garage.core import constants
from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


def _require_key():
    if not settings.monday_api_key:
        raise ConfigError("Monday: monday_api_key not configured")


class MondayClient:
    """Monday.com GraphQL client. Inject `service_client` (a ServiceClient) for tests."""

    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Monday", base_url="https://api.monday.com",
            headers_fn=lambda: {"Authorization": settings.monday_api_key, "Content-Type": "application/json"},
        )

    def _gql(self, query, variables=None, action="query"):
        """POST a GraphQL op to Monday's /v2 and return body['data']. Monday returns HTTP 200 even on
        GraphQL errors, so check body['errors'] explicitly (ServiceClient handled transport/HTTP)."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        body = self._client.post("/v2", json=payload)
        if body.get("errors"):
            raise ServiceError("Monday", f"{action} error: {body.get('errors')}")
        return body.get("data") or {}

    def _users_by_email(self, emails):
        """Resolve Monday user ids for `emails` via the users(emails:) query.
        Returns {email_lower: user_id}; emails not on the account are omitted (best-effort)."""
        wanted = sorted({e.strip().lower() for e in emails if e and e.strip()})
        if not wanted:
            return {}
        # emails arg is [String!] on Monday's schema — a [String] variable is rejected
        # ("Variable $e of type [String] used in position expecting type [String!]").
        data = self._gql("query($e:[String!]){users(emails:$e){id email}}", {"e": wanted}, "users")
        out = {}
        for u in data.get("users") or []:
            em = (u.get("email") or "").strip().lower()
            if em and u.get("id") is not None:
                out[em] = str(u["id"])
        return out

    def resolve_user_ids(self, emails):
        """Public: resolve owner emails to Monday user ids (list) for create_board's board_owner_ids
        (create_board takes ids, not emails). Callers that also add_owners should prefer resolve_user_map
        + .values() so the single lookup is shared across both."""
        return list(self._users_by_email(emails).values())

    def find_board_by_name(self, workspace_id, name):
        """Return an existing board id by exact name within `workspace_id` (bounded pagination), else
        None. Makes the per-tenant board create idempotent under redelivery even if the id ref wasn't
        persisted."""
        _require_key()
        page = 1
        for _ in range(20):  # 20 * 100 = 2000-board ceiling
            data = self._gql("query($w:[ID!],$l:Int!,$p:Int!){boards(workspace_ids:$w,limit:$l,page:$p){id name}}",
                        {"w": [str(workspace_id)], "l": 100, "p": page}, "boards")
            chunk = data.get("boards") or []
            for b in chunk:
                if (b.get("name") or "") == name and b.get("id") is not None:
                    return str(b["id"])
            if len(chunk) < 100:
                break
            page += 1
        return None

    def find_workspace_by_name(self, name):
        """Return an existing workspace id by exact name (bounded pagination), else None. Makes the
        per-tenant create idempotent under redelivery even if the id ref wasn't persisted."""
        _require_key()
        page = 1
        for _ in range(20):  # 20 * 100 = 2000-workspace ceiling
            data = self._gql("query($l:Int!,$p:Int!){workspaces(limit:$l,page:$p){id name}}",
                        {"l": 100, "p": page}, "workspaces")
            chunk = data.get("workspaces") or []
            for w in chunk:
                if (w.get("name") or "") == name and w.get("id") is not None:
                    return str(w["id"])
            if len(chunk) < 100:
                break
            page += 1
        return None

    def create_workspace(self, name, description=None):
        """Create a Monday workspace; return its id. kind = constants.MONDAY_WORKSPACE_KIND."""
        _require_key()
        kind = constants.MONDAY_WORKSPACE_KIND
        query = ("mutation($n:String!,$k:WorkspaceKind!,$d:String)"
                 "{create_workspace(name:$n,kind:$k,description:$d){id}}")
        data = self._gql(query, {"n": name, "k": kind, "d": description}, "create_workspace")
        wid = (data.get("create_workspace") or {}).get("id")
        if wid is None:
            raise ServiceError("Monday", "create_workspace returned no id")
        return str(wid)

    def create_board_from_template(self, workspace_id, name, template_id, owner_ids=None, kind="public"):
        """Create a board in `workspace_id` from a Monday template, with `owner_ids` as board owners.
        Returns the new board id. board_owner_ids sets owners at creation (no extra call)."""
        _require_key()
        query = ("mutation($n:String!,$k:BoardKind!,$w:ID!,$t:ID,$o:[ID!])"
                 "{create_board(board_name:$n,board_kind:$k,workspace_id:$w,template_id:$t,board_owner_ids:$o){id}}")
        variables = {"n": name, "k": kind, "w": str(workspace_id), "t": str(template_id),
                     "o": [str(u) for u in (owner_ids or [])]}
        data = self._gql(query, variables, "create_board")
        bid = (data.get("create_board") or {}).get("id")
        if bid is None:
            raise ServiceError("Monday", "create_board returned no id")
        return str(bid)

    # Idempotency substrings swallowed by the subscriber mutations. Adds tolerate "already"
    # (already subscribed); removes tolerate the not-subscribed variants too (a user not on the
    # target is a no-op). Kept as method-specific sets so each mutation's exact tolerance is explicit.
    _ADD_SWALLOW = frozenset({"already"})
    _REMOVE_SWALLOW = frozenset({"not a subscriber", "not subscribed", "already"})

    def resolve_user_map(self, emails):
        """Public: resolve owner emails to a {email_lower: user_id} map, once, so a caller can hand the
        SAME map to add/remove (below) + derive uids for create_board — no repeat users(emails:) lookup."""
        return self._users_by_email(emails)

    def _subscriber_op(self, emails, query, variables_for, action, *,
                       ok_msg, noop_msg, swallow, warn, user_map=None):
        """Shared scaffold for the 4 add/remove subscriber ops: resolve emails → uids (unless the caller
        already resolved them and passes `user_map`), early-return if none resolve, run the mutation,
        swallow the idempotency errors in `swallow`, and return a summary. `variables_for(uids)` builds
        the GraphQL variables; `ok_msg(uids)`/`noop_msg(uids)` build the return strings.

        `warn` (add-only) surfaces owners that didn't resolve to a Monday user — the remove variants
        deliberately don't warn (a re-assignment removes a set that may not all resolve). warn='log_empty'
        additionally logs an info line when nothing resolves (mirrors the old add_owners behaviour)."""
        _require_key()
        if user_map is None:
            user_map = self._users_by_email(emails)
        if warn:
            # Surface any configured owner that DIDN'T resolve to a Monday user — else a missing owner
            # (email not on the Monday account / typo) is silently dropped while the rest are added (this
            # bit us with Paaras once). A warning makes the gap visible instead of a silent partial add.
            missing = {e.strip().lower() for e in emails if e and e.strip()} - set(user_map)
            if missing:
                logger.warning("[kickstart] monday %s: %d owner email(s) did NOT resolve to a Monday user — skipped: %s",
                                action, len(missing), sorted(missing))
        if not user_map:
            if warn == "log_empty":
                logger.info("[kickstart] monday %s: no Monday users resolved for %s — nothing to add", action, emails)
            return "skipped: no matching Monday users"
        uids = list(user_map.values())
        try:
            self._gql(query, variables_for(uids), action)
        except ServiceError as e:
            low = str(e).lower()
            if not any(s in low for s in swallow):
                raise
            return noop_msg(uids)
        return ok_msg(uids)

    def add_owners(self, workspace_id, emails, user_map=None):
        """Add the resolved Monday users for `emails` to the workspace as OWNERS. Idempotent: users
        already subscribed are a no-op (Monday's 'already'-type errors are swallowed). Returns a summary.
        Pass `user_map` (from resolve_user_map) to reuse an already-resolved lookup."""
        return self._subscriber_op(
            emails,
            "mutation($w:ID!,$u:[ID!]!,$k:WorkspaceSubscriberKind!)"
            "{add_users_to_workspace(workspace_id:$w,user_ids:$u,kind:$k){id}}",
            lambda uids: {"w": str(workspace_id), "u": uids, "k": "owner"},
            "add_users_to_workspace",
            ok_msg=lambda uids: f"monday: added {len(uids)} owner(s) to workspace {workspace_id}",
            noop_msg=lambda uids: f"noop: {len(uids)} already owners of workspace {workspace_id}",
            swallow=self._ADD_SWALLOW, warn="log_empty", user_map=user_map)

    def add_board_owners(self, board_id, emails, user_map=None):
        """Add the resolved Monday users for `emails` to the board as OWNERS. Idempotent: users already
        subscribed are a no-op (Monday's 'already'-type errors swallowed). Returns a summary.
        Pass `user_map` (from resolve_user_map) to reuse an already-resolved lookup."""
        return self._subscriber_op(
            emails,
            "mutation($b:ID!,$u:[ID!]!,$k:BoardSubscriberKind)"
            "{add_users_to_board(board_id:$b,user_ids:$u,kind:$k){id}}",
            lambda uids: {"b": str(board_id), "u": uids, "k": "owner"},
            "add_users_to_board",
            ok_msg=lambda uids: f"monday: added {len(uids)} owner(s) to board {board_id}",
            noop_msg=lambda uids: f"noop: {len(uids)} already owners of board {board_id}",
            swallow=self._ADD_SWALLOW, warn=True, user_map=user_map)

    def remove_users(self, workspace_id, emails, user_map=None):
        """Remove the resolved Monday users for `emails` from the workspace (re-assignment reconcile).
        Idempotent: a user not in the workspace is a no-op ('not a subscriber' swallowed). Returns a summary.
        Pass `user_map` (from resolve_user_map) to reuse an already-resolved lookup."""
        return self._subscriber_op(
            emails,
            "mutation($w:ID!,$u:[ID!]!)"
            "{delete_users_from_workspace(workspace_id:$w,user_ids:$u){id}}",
            lambda uids: {"w": str(workspace_id), "u": uids},
            "delete_users_from_workspace",
            ok_msg=lambda uids: f"monday: removed {len(uids)} user(s) from workspace {workspace_id}",
            noop_msg=lambda uids: f"noop: {len(uids)} not in workspace {workspace_id}",
            swallow=self._REMOVE_SWALLOW, warn=False, user_map=user_map)

    def remove_board_users(self, board_id, emails, user_map=None):
        """Remove the resolved Monday users for `emails` from the board (re-assignment reconcile).
        Idempotent: a user not subscribed is a no-op ('not a subscriber' swallowed). Returns a summary.
        Pass `user_map` (from resolve_user_map) to reuse an already-resolved lookup."""
        return self._subscriber_op(
            emails,
            "mutation($b:ID!,$u:[ID!]!)"
            "{delete_subscribers_from_board(board_id:$b,user_ids:$u){id}}",
            lambda uids: {"b": str(board_id), "u": uids},
            "delete_subscribers_from_board",
            ok_msg=lambda uids: f"monday: removed {len(uids)} user(s) from board {board_id}",
            noop_msg=lambda uids: f"noop: {len(uids)} not on board {board_id}",
            swallow=self._REMOVE_SWALLOW, warn=False, user_map=user_map)


monday_client = MondayClient()
