"""Cross-phase kickoff-step helpers shared by register / completion / assign (and verify):
the kickoff deep-link, the Slack channel-id resolver, and the flat channel-bookmark helper."""

import logging

from dtapp.garage.kickstart.clients import slack_client
from dtapp.garage.kickstart.recipients import recipients
from dtapp.garage.kickstart.repositories import kickoff_repo

logger = logging.getLogger(__name__)


def _kickoff_detail_url(koid) -> str:
    """The Kickstart-module detail page for a kickoff on the Garage FE (garage-fe → /kickstart/<koid>).
    Single source for the deep links posted to Slack / Linear / Paaras DM / verify alerts."""
    return f"{recipients.form_base_url}/kickstart/{koid}"


def _resolve_channel_id(koid, cfg):
    """channel_id from cfg, or recover it by name (Slack lookup) and persist. Self-heals the rename +
    channel-add steps when announce stored only the name (e.g. create hit name_taken and the
    reuse-by-name lookup didn't run), so they don't silently no-op on a present-but-idless channel."""
    cfg = cfg or {}
    channel_id = cfg.get("channel_id")
    if channel_id:
        return channel_id
    name = cfg.get("name")
    if not name:
        return None
    try:
        found = slack_client.find_channel_by_name(name)
    except Exception as e:  # noqa: BLE001 — best-effort recovery; never crash the caller
        logger.warning("[kickstart] %s: could not resolve channel id by name '%s': %s", koid, name, e)
        return None
    if found:
        kickoff_repo.set_slack_channel_config(koid, {"channel_id": found})  # persist so later steps skip the lookup
        logger.info("[kickstart] %s: recovered channel id %s by name '%s'", koid, found, name)
    return found


def _channel_id_or_load(koid, channel_id):
    """The channel id: prefer the one threaded in from this provisioning run (post_tenant_created_slack
    just created + persisted it), else fall back to re-loading the kickoff row + resolving from
    slack_channel_config. The fallback preserves idempotent-resume: a redelivery / re-drive that skips
    channel-create (so no id was threaded) still resolves the id from the DB."""
    if channel_id:
        return channel_id
    kickoff = kickoff_repo.get_kickoff(koid)
    cfg = (kickoff.slack_channel_config if kickoff else None) or {}
    return _resolve_channel_id(koid, cfg)


def _add_channel_bookmark(koid, title, link, emoji=None, channel_id=None):
    """Best-effort: add a flat link bookmark to the kickoff's channel. `channel_id` is reused when the
    caller already has it (this provisioning run created the channel); otherwise it's resolved from the
    kickoff's slack_channel_config. Used at provisioning for the Monday-workspace + kickoff-site links.
    Flat — Slack's API can't nest bookmarks in folders (see slack_client.add_bookmark)."""
    if not link:
        return "skipped: no link"
    channel_id = _channel_id_or_load(koid, channel_id)
    if not channel_id:
        return "skipped: no channel"
    try:
        slack_client.add_bookmark(channel_id, title, link=link, emoji=emoji)
        return f"bookmarked {title}"
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning("[kickstart] channel bookmark '%s' failed for %s: %s", title, koid, e)
        return f"failed: {e}"
