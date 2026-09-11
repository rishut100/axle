import json
import logging
import re
import time

import requests  # for the raw PUT to Slack's pre-signed file-upload URL (no bearer header there)

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.core.http_util import ServiceClient

logger = logging.getLogger(__name__)


class SlackClient:
    def __init__(self, service_client=None):
        self._client = service_client or ServiceClient(
            "Slack", base_url="https://slack.com/api",
            headers_fn=lambda: {"Authorization": f"Bearer {settings.kickstart_slack_bot_token}"},
        )

    @staticmethod
    def slugify(value: str) -> str:
        """Lowercase, hyphenate, strip to Slack-safe channel chars (max 80 incl. prefix)."""
        s = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
        return s[:50] or "kickoff"

    def _configured(self) -> bool:
        return bool(settings.kickstart_slack_bot_token)

    def _dryrun(self, endpoint, payload):
        """When unconfigured + dryrun mode: log the payload and return a marker string."""
        logger.info("[kickstart] DRYRUN slack %s — would send: %s", endpoint, payload)
        return f"dryrun: slack {endpoint} (token unconfigured)"

    def _require_token(self):
        """Fail-loud unless dryrun-unconfigured is on; returns True if caller should dry-run."""
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("Slack: KICKSTART_SLACK_BOT_TOKEN not set")

    def _check(self, endpoint, body):
        """Slack returns HTTP 200 even on logical errors → check the body-level `ok` flag.
        (ServiceClient already handled transport + HTTP-status errors.)"""
        if not body.get("ok"):
            raise ServiceError("Slack", f"error ({endpoint}): {body.get('error')}")
        return body

    def _post(self, endpoint, payload):
        return self._check(endpoint, self._client.post(f"/{endpoint}", json=payload))

    def _get(self, endpoint, params):
        """For query/form-style methods like users.lookupByEmail that ignore a JSON body."""
        return self._check(endpoint, self._client.get(f"/{endpoint}", params=params))

    def _find_channel_by_name(self, name):
        """Look up an existing channel id by exact name (bounded pagination). Needs channels:read /
        groups:read on the bot; returns None if not found or the scope is missing."""
        cursor = None
        for _ in range(12):
            params = {"limit": 200, "exclude_archived": "true", "types": "public_channel,private_channel"}
            if cursor:
                params["cursor"] = cursor
            body = self._get("conversations.list", params)
            for ch in body.get("channels", []):
                if ch.get("name") == name:
                    return ch["id"]
            cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                break
        return None

    def create_channel(self, name):
        """Create a public Slack channel; return its channel id. Idempotent: if the name is already
        taken (e.g. a prior rolled-back register created it), reuse the existing channel instead of failing."""
        if self._require_token():
            return self._dryrun("conversations.create", {"name": name, "is_private": False})
        try:
            body = self._post("conversations.create", {"name": name, "is_private": False})
            return body["channel"]["id"]
        except ServiceError as e:
            if "name_taken" not in str(e):
                raise
            existing = self._find_channel_by_name(name)
            if existing:
                logger.info("[kickstart] channel '%s' already exists → reusing %s", name, existing)
                return existing
            raise  # couldn't reuse (missing channels:read scope?) → caller decides (announce is best-effort)

    def find_channel_by_name(self, name):
        """Public: resolve a channel id by exact name (None if not found / unconfigured). Lets callers
        recover a channel_id that was never persisted (e.g. create hit name_taken and reuse didn't run)."""
        if not name or self._require_token():
            return None
        return self._find_channel_by_name(name)

    def rename_channel(self, channel_id, new_name):
        """Rename an existing Slack channel by id."""
        if self._require_token():
            return self._dryrun("conversations.rename", {"channel": channel_id, "name": new_name})
        self._post("conversations.rename", {"channel": channel_id, "name": new_name})
        return f"renamed {channel_id} to {new_name}"

    def join_channel(self, channel_id):
        """Join a public channel (no-op if already a member). Call before rename/bookmark."""
        if self._require_token():
            return self._dryrun("conversations.join", {"channel": channel_id})
        self._post("conversations.join", {"channel": channel_id})
        return f"joined {channel_id}"

    def set_channel_purpose(self, channel_id, purpose):
        """Set the channel's purpose/description (what the channel is about). Idempotent — setting the
        same purpose is a no-op. Needs channels:manage on the bot."""
        if self._require_token():
            return self._dryrun("conversations.setPurpose", {"channel": channel_id, "purpose": purpose})
        self._post("conversations.setPurpose", {"channel": channel_id, "purpose": purpose})
        return f"purpose set on {channel_id}"

    def post_message(self, channel_id, text):
        """Post a message to a channel by id."""
        if self._require_token():
            return self._dryrun("chat.postMessage", {"channel": channel_id, "text": text})
        body = self._post("chat.postMessage", {"channel": channel_id, "text": text})
        return f"slack ts={body.get('ts')}"

    def lookup_user_by_email(self, email):
        """Resolve a Slack user id from an email address."""
        if self._require_token():
            return self._dryrun("users.lookupByEmail", {"email": email})
        body = self._get("users.lookupByEmail", {"email": email})
        return body["user"]["id"]

    def resolve_user_id(self, email):
        """Resolve an email to a real Slack user id, or None if unresolvable (no email, dryrun marker,
        lookup miss, API error) — so callers just check truthiness (`if uid: mention(uid)`)."""
        if not email:
            return None
        try:
            uid = self.lookup_user_by_email(email)
        except Exception as e:  # noqa: BLE001 — a lookup miss must never block the caller
            logger.warning("[kickstart] slack user lookup failed for %s: %s", email, e)
            return None
        return uid if isinstance(uid, str) and uid[:1] in ("U", "W") else None  # dryrun/local returns a marker

    def send_dm(self, target_id, text):
        """Send `text` to a person OR a channel. A channel id (C…/G…) posts straight to the channel; any
        other id (a user U…/W…) opens a DM and posts there. Lets the assign-consultant notice target either
        Paaras directly or a shared channel via env (KICKSTART_ASSIGN_NOTIFY) with no code branch."""
        if self._require_token():
            return self._dryrun("conversations.open+chat.postMessage", {"target": target_id, "text": text})
        if str(target_id).startswith(("C", "G")):  # channel id → post directly (no DM open)
            body = self._post("chat.postMessage", {"channel": target_id, "text": text})
            return f"channel ts={body.get('ts')}"
        open_body = self._post("conversations.open", {"users": target_id})
        dm_channel = open_body["channel"]["id"]
        body = self._post("chat.postMessage", {"channel": dm_channel, "text": text})
        return f"dm ts={body.get('ts')}"

    def dm_email(self, email, text):
        """Resolve a user by email, open a DM and post a message."""
        if self._require_token():
            return self._dryrun("users.lookupByEmail+dm", {"email": email, "text": text})
        user_id = self.lookup_user_by_email(email)
        return self.send_dm(user_id, text)

    def add_bookmark(self, channel_id, title, link=None, emoji=None):
        """Add a flat link bookmark to a channel's bookmark bar. NOTE: Slack's bookmarks API can't create
        FOLDERS (type=folder is coerced to a plain link + nesting via parent_id 400s with
        invalid_parent_type), so all kickoff bookmarks are flat — the "Context"/"Timezone" folder grouping
        is UI-only. A link is mandatory; fall back to a placeholder anchor."""
        if self._require_token():
            return self._dryrun("bookmarks.add", {"channel_id": channel_id, "title": title, "link": link})
        payload = {"channel_id": channel_id, "title": title, "type": "link", "link": link or "https://example.com"}
        if emoji:
            payload["emoji"] = emoji
        body = self._post("bookmarks.add", payload)
        return body.get("bookmark", {}).get("id")

    def invite_to_channel(self, channel_id, user_ids):
        """Invite one or more users (app bot users) to a channel. user_ids: str or list.
        Idempotent for reconcile use: inviting someone already in the channel is a no-op
        (Slack's already_in_channel is swallowed)."""
        if self._require_token():
            return self._dryrun("conversations.invite", {"channel": channel_id, "users": user_ids})
        users = ",".join(user_ids) if isinstance(user_ids, (list, tuple)) else user_ids
        try:
            self._post("conversations.invite", {"channel": channel_id, "users": users})
        except ServiceError as e:
            if "already_in_channel" in str(e):
                return f"noop: {users} already in {channel_id}"
            raise
        return f"invited {users} to {channel_id}"

    def upload_file(self, channel_id, filename, content: bytes, title=None, initial_comment=None):
        """Upload a file's bytes into a channel via Slack's external-upload flow (reserve URL → PUT bytes →
        complete+share). `initial_comment` (optional) is posted as the message accompanying the file. Returns
        the file's permalink (or a marker). Needs files:write on the bot — a missing scope surfaces as a
        ServiceError the caller swallows (best-effort)."""
        if self._require_token():
            return self._dryrun("files.upload", {"channel": channel_id, "filename": filename, "bytes": len(content or b"")})
        # 1. reserve a pre-signed upload URL
        body = self._get("files.getUploadURLExternal", {"filename": filename, "length": len(content or b"")})
        upload_url, file_id = body.get("upload_url"), body.get("file_id")
        if not upload_url or not file_id:
            raise ServiceError("Slack", "files.getUploadURLExternal returned no upload_url/file_id")
        # 2. PUT the raw bytes to the pre-signed URL (no Slack bearer header here)
        r = requests.post(upload_url, data=content, timeout=30)
        if r.status_code >= 300:
            raise ServiceError("Slack", f"file upload PUT failed: HTTP {r.status_code}")
        # 3. complete the upload + share it into the channel. `files` is a JSON-encoded array (Slack contract).
        payload = {"files": json.dumps([{"id": file_id, "title": title or filename}]), "channel_id": channel_id}
        if initial_comment:
            payload["initial_comment"] = initial_comment
        done = self._post("files.completeUploadExternal", payload)
        files = done.get("files") or [{}]
        return files[0].get("permalink") or f"uploaded {filename} to {channel_id}"

    def create_channel_canvas(self, channel_id, markdown, title=None):
        """Create the channel's canvas with a title + markdown content (e.g. the tenant time zone).
        Slack does NOT derive the canvas title from a body H1 — it must be passed explicitly. Returns a
        marker string. Needs canvases:write on the bot — a missing scope surfaces as a ServiceError the
        caller swallows (best-effort). Belt-and-suspenders for reliability (Kickstart is low-scale, so a
        blocking wait is a non-issue): the caller DEFERS this to the end of provisioning (see
        completion._finalize_timezone_canvas) so the channel's canvas backend has time to provision, AND —
        should it still not be ready — we retry `canvas_tab_creation_failed` with a short backoff. Other
        errors surface immediately; a final still-failing canvas is swallowed by the caller."""
        if self._require_token():
            return self._dryrun("conversations.canvases.create", {"channel": channel_id, "len": len(markdown or "")})
        payload = {"channel_id": channel_id, "document_content": {"type": "markdown", "markdown": markdown}}
        if title:
            payload["title"] = title
        delays = [3, 5, 8]  # up to 4 attempts; only the not-yet-provisioned error is retried
        for attempt in range(len(delays) + 1):
            try:
                body = self._post("conversations.canvases.create", payload)
                return f"canvas {body.get('canvas_id')} on {channel_id}"
            except ServiceError as e:
                if "canvas_tab_creation_failed" not in str(e) or attempt == len(delays):
                    raise
                logger.info("[kickstart] channel %s canvas not ready yet (attempt %d) — retrying in %ds",
                            channel_id, attempt + 1, delays[attempt])
                time.sleep(delays[attempt])

    def remove_from_channel(self, channel_id, user_id):
        """Kick a single user from a channel (conversations.kick; needs channels:manage on the bot).
        Idempotent for reconcile use: kicking someone not in the channel is a no-op (Slack's
        not_in_channel is swallowed)."""
        if self._require_token():
            return self._dryrun("conversations.kick", {"channel": channel_id, "user": user_id})
        try:
            self._post("conversations.kick", {"channel": channel_id, "user": user_id})
        except ServiceError as e:
            if "not_in_channel" in str(e):
                return f"noop: {user_id} not in {channel_id}"
            raise
        return f"removed {user_id} from {channel_id}"


slack_client = SlackClient()
