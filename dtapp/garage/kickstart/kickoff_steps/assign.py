"""Phase 4 post-assign-consultant: add the assigned consultant / analyst / pod-lead to the internal
Slack channel + as Monday board-item owners. Runs async (handle_assign) while status
CONSULTANT_ASSIGNED; each task is idempotent (step-flag guarded) + best-effort."""

import logging

from dtapp.garage.kickstart.clients import slack_client, monday_client, linear_client
from dtapp.garage.core.config import settings
from dtapp.garage.kickstart.repositories import kickoff_repo, consultant_repo
from dtapp.garage.kickstart import notify

from dtapp.garage.kickstart.kickoff_steps._runner import dryrun as _dryrun, run_step, step_done, best_effort
from dtapp.garage.kickstart.kickoff_steps.common import _resolve_channel_id

logger = logging.getLogger(__name__)


def assignment_emails(kickoff) -> list:
    """Resolve the kickoff's consultant / analyst / pod-lead FK ids → unique, set emails.
    Soft-deleted people still resolve via get_consultant(id), so a departed assignee is still added."""
    ids = [kickoff.solution_consultant_id, kickoff.analyst_id, kickoff.pod_lead_id]
    emails = []
    for cid in ids:
        if not cid:
            continue
        c = consultant_repo.get_consultant(cid)
        if c and c.email and c.email not in emails:
            emails.append(c.email)
    return emails


def _channel_kick(koid, channel_id, removed):
    """Reconcile: kick the people removed by a re-assignment from the internal Slack channel.
    Best-effort per person (resolve email → user id, conversations.kick — a not-in-channel kick is a
    no-op), so a partial failure / redelivery can't block. `removed` is data['_assign_removed']."""
    for email in removed or []:
        with best_effort(f"channel_kick: remove {email} from channel", koid):
            slack_client.remove_from_channel(channel_id, slack_client.lookup_user_by_email(email))
            logger.info("[kickstart] channel_kick: removed %s from channel for %s", email, koid)


def _channel_add(koid, data, channel_cfg, emails, removed):
    """Reconcile the kickoff's internal Slack channel to the current assignees: kick the people removed
    by a re-assignment (`removed`), then add the current consultant / analyst / pod-lead. channel_id
    comes from the kickoff's slack_channel_config (a top-level row column). Each kick/invite is
    best-effort in its own try (invite is idempotent — already-in-channel is a no-op; kick of a
    not-in-channel user is a no-op), so a redelivery / partial failure can't duplicate or block. Guarded
    by the assign_channel_add step flag — cleared on re-assign so this re-runs with the new set. The
    removed-stash is cleared once by run_assign_steps after both reconcile consumers have run."""
    def _body():
        cfg = channel_cfg or {}
        channel_id = _resolve_channel_id(koid, cfg)
        if not channel_id:
            logger.info("[kickstart] channel_add skipped for %s: no channel_id (name='%s')", koid, cfg.get("name"))
            return "skipped: no channel_id"
        _channel_kick(koid, channel_id, removed)
        for email in emails:
            with best_effort(f"channel_add: add {email} to channel", koid):
                slack_client.invite_to_channel(channel_id, slack_client.lookup_user_by_email(email))
                logger.info("[kickstart] channel_add: added %s to channel for %s", email, koid)
        return f"channel_add: processed {len(emails)} people (kicked {len(removed)}) for {channel_id}"
    dry = settings.dryrun_unconfigured and not settings.kickstart_slack_bot_token
    return run_step(koid, data, "assign_channel_add", _body,
                    skip_result="skipped: already added to channel",
                    dryrun_when=dry,
                    dryrun_result=(_dryrun("assign_channel_add", {"emails": emails, "removed": removed}) if dry else None))


def _monday_workspace_id(data):
    """The per-tenant Monday workspace id from references (created at provisioning). None if absent."""
    return (data.get("references") or {}).get("monday_workspace_id")


def _monday_board_id(data):
    """The per-tenant Monday board id from references (created at provisioning). None if absent."""
    return (data.get("references") or {}).get("monday_board_id")


def _monday_owners(koid, data, emails, removed):
    """Reconcile the kickoff's Monday workspace owners to the current assignees: remove the people
    dropped by a re-assignment (`removed`), then add the current consultant / analyst / pod-lead as
    owners (mirrors the Slack channel reconcile). Best-effort + guarded by the assign_monday_owners
    step flag (cleared on re-assign so this re-runs with the new set). Honors the dryrun guard."""
    def _body():
        workspace_id = _monday_workspace_id(data)
        board_id = _monday_board_id(data)
        if not workspace_id:
            logger.info("[kickstart] monday_owners skipped for %s: no monday workspace id", koid)
            return "skipped: no monday workspace"
        if removed:
            # Resolve the removed set ONCE and reuse across workspace + board (no repeat lookup).
            removed_map = monday_client.resolve_user_map(removed)
            monday_client.remove_users(workspace_id, removed, user_map=removed_map)
            if board_id:
                monday_client.remove_board_users(board_id, removed, user_map=removed_map)
            logger.info("[kickstart] monday_owners: removed %s from workspace+board for %s", removed, koid)
        if not emails:
            return "skipped: no assignees"
        # Resolve the assignee set ONCE and reuse across workspace + board (no repeat lookup).
        emails_map = monday_client.resolve_user_map(emails)
        result = monday_client.add_owners(workspace_id, emails, user_map=emails_map)
        if board_id:
            monday_client.add_board_owners(board_id, emails, user_map=emails_map)
        return result
    dry = settings.dryrun_unconfigured and not settings.monday_api_key
    return run_step(koid, data, "assign_monday_owners", _body,
                    skip_result="skipped: owners already set",
                    dryrun_when=dry,
                    dryrun_result=(_dryrun("assign_monday_owners", {"koid": koid, "emails": emails, "removed": removed}) if dry else None),
                    best_effort=True, task_key="monday_owners", stage="consultant_assigned")


def _consultant_email_name(kickoff):
    """The assigned solution consultant's (email, name) — the deck sub-issue is reassigned to this
    person + they're pinged. (None, None) when no consultant is set."""
    cid = kickoff.solution_consultant_id
    if not cid:
        return None, None
    c = consultant_repo.get_consultant(cid)
    if not c or not c.email:
        return None, None
    return c.email, (c.name or c.email)


def _deck_linear(koid, data, kickoff, channel_cfg):
    """On consultant assignment: reassign the kickoff's 'Update Kickoff Deck' Linear sub-issue to the
    assigned consultant, and ping them in the internal Slack channel with the issue link. Both
    best-effort. Needs deck_linear_id/url in references (stashed at tree creation) — an older kickoff
    without it is skipped. Guarded by the assign_deck_linear step flag (cleared on re-assign so it
    re-runs for the new consultant)."""
    def _body():
        refs = data.get("references") or {}
        deck_id, deck_url = refs.get("deck_linear_id"), refs.get("deck_linear_url")
        if not deck_id:
            logger.info("[kickstart] deck_linear skipped for %s: no deck_linear_id in references", koid)
            return "skipped: no deck issue"
        email, name = _consultant_email_name(kickoff)
        if not email:
            logger.info("[kickstart] deck_linear skipped for %s: no consultant", koid)
            return "skipped: no consultant"
        # Reassign the deck sub-issue → the consultant. Best-effort but OBSERVABLE (record_task_error
        # below); deliberately NOT retried — an unresolved Linear id is deterministic (retry would just
        # DLQ) and the ping is non-idempotent (retry would double-post). So record + move on, don't raise.
        errors = []
        reassigned = False
        uid = linear_client.resolve_user_id(email)
        if uid:
            try:
                linear_client.update_issue(deck_id, assignee_id=uid)
                reassigned = True
            except Exception as e:  # noqa: BLE001 — best-effort
                logger.warning("[kickstart] deck_linear reassign failed for %s: %s", koid, e)
                errors.append(f"reassign: {e}")
        else:
            errors.append("reassign: no Linear user for the consultant")
        # Ping the consultant in the internal channel with the deck issue link.
        pinged = False
        channel_id = _resolve_channel_id(koid, channel_cfg or {})
        if channel_id and deck_url:
            uid = slack_client.resolve_user_id(email)  # real id or None (lookup miss/dryrun swallowed)
            mention = notify.mention(uid) if uid else name
            try:
                slack_client.post_message(
                    channel_id, f"{mention} Please address this linear at the earliest. {deck_url}")
                pinged = True
            except Exception as e:  # noqa: BLE001 — best-effort ping
                logger.warning("[kickstart] deck_linear ping failed for %s: %s", koid, e)
                errors.append(f"ping: {e}")
        # Surface partial failures (mirrors _create_academy_linear) so a swallowed failure is
        # observable as a task error — without raising, so the step is still marked done.
        if errors:
            kickoff_repo.record_task_error(koid, "consultant_assigned", "deck_linear", "; ".join(errors))
        return f"deck_linear ({email}): reassigned={reassigned}, pinged={pinged}"
    dry = settings.dryrun_unconfigured and not settings.linear_api_key
    return run_step(koid, data, "assign_deck_linear", _body,
                    skip_result="skipped: deck already handled",
                    dryrun_when=dry,
                    dryrun_result=(_dryrun("assign_deck_linear", {"koid": koid}) if dry else None))


def run_assign_steps(koid, kickoff) -> None:
    """Post-assign work: reconcile the assigned consultant / analyst / pod-lead onto the internal
    Slack channel + the Monday workspace owners (both kick the re-assignment's removed people, then add
    the current set). Both idempotent (step-flag guarded) + best-effort. `kickoff` is the loaded row
    (for the FK ids); `kickoff.data` carries slack_channel_config + references + the removed-stash."""
    data = kickoff.data
    emails = assignment_emails(kickoff)
    removed = data.get("_assign_removed") or []
    _channel_add(koid, data, kickoff.slack_channel_config, emails, removed)
    _monday_owners(koid, data, emails, removed)
    # Reassign the "Update Kickoff Deck" sub-issue to the consultant + ping them in the int channel.
    _deck_linear(koid, data, kickoff, kickoff.slack_channel_config)
    # Both reconcile consumers have run — clear the removed-stash once so a redelivery doesn't re-kick.
    kickoff_repo.set_assign_removed(koid, [])
