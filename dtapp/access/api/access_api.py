import logging

from flask import Blueprint, request

from dtapp.garage.core.auth import require_drivetrainer
from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import BadRequestError, ForbiddenError, NotFoundError, register_error_handlers
from dtapp.access.enums import AccessEventStatus
from dtapp.access.matrix import matrix_repo
from dtapp.access.repositories import access_event_repo
from dtapp.access.schemas.access_event import NormalizedAccessEvent, from_keka_payload
from dtapp.access.services import event_service

logger = logging.getLogger(__name__)

access_bp = Blueprint("access", __name__)
register_error_handlers(access_bp)


def _require_keka_secret():
    """Shared-secret check on the Keka webhook (Keka has no HMAC signing to verify against, unlike
    Slack). Blank secret in prod -> fail closed (never silently accept an unverified webhook in prod);
    blank in non-prod -> accept unauthenticated (local testing without provisioning a real secret)."""
    if not settings.keka_webhook_secret:
        if settings.is_prod:
            raise ForbiddenError("keka_webhook_secret not configured — refusing unauthenticated webhook in prod")
        return
    provided = request.headers.get("X-Webhook-Secret", "")
    if provided != settings.keka_webhook_secret:
        raise ForbiddenError("invalid webhook secret")


@access_bp.route('/access/webhook/keka', methods=['POST'])
def access_keka_webhook():
    """Primary trigger (decision #1). Keka's actual field names are isolated inside
    schemas.access_event.from_keka_payload — confirm that mapping against a real payload sample
    before go-live."""
    _require_keka_secret()
    payload = request.get_json(force=True) or {}
    event = from_keka_payload(payload)
    aeid = event_service.ingest_event(event)
    return {"aeid": aeid}, 202


@access_bp.route('/access/event', methods=['POST'])
@require_drivetrainer
def access_manual_event():
    """Manual-trigger/backstop route — hand-fire an event when Keka is late/wrong, or for local
    testing without Keka creds configured. Body is the NormalizedAccessEvent shape directly."""
    body = request.get_json(force=True) or {}
    event = NormalizedAccessEvent(**{**body, "source": "manual_api"})
    aeid = event_service.ingest_event(event)
    return {"aeid": aeid}, 202


@access_bp.route('/access/event/<aeid>', methods=['GET'])
@require_drivetrainer
def access_get_event(aeid):
    event = access_event_repo.get_event(aeid)
    if event is None:
        raise NotFoundError(f"no access_event {aeid}")
    return event.model_dump(mode="json"), 200


@access_bp.route('/access/event/<aeid>/tool/<tool>/complete', methods=['POST'])
@require_drivetrainer
def access_mark_tool_complete(aeid, tool):
    """Manual backstop for the Slack-reaction "done" signal (decision #6) — for when a reaction gets
    missed or an owner prefers to just tell someone directly."""
    event = access_event_repo.get_event(aeid)
    if event is None:
        raise NotFoundError(f"no access_event {aeid}")
    if tool not in event.checklist:
        raise BadRequestError(f"{tool!r} is not in the checklist for {aeid}")
    access_event_repo.set_checklist_item(aeid, tool, status="done", completed_by=request.principal)
    final_status = access_event_repo.rollup_status(aeid)
    access_event_repo.set_status(aeid, final_status)

    entry = event.checklist.get(tool, {})
    issue_id, team_id = entry.get("linear_issue_id"), entry.get("linear_team_id")
    if issue_id and team_id:
        from dtapp.access.notify import best_effort
        from dtapp.access.clients import linear_client
        with best_effort(f"close_linear_issue:{tool}", aeid):
            linear_client.close_issue(issue_id, team_id)

    return {"aeid": aeid, "tool": tool, "status": "done"}, 200


# ── Role -> tool -> owner matrix admin CRUD (decision #2) ──

@access_bp.route('/access/matrix', methods=['GET'])
@require_drivetrainer
def access_list_matrix():
    team = request.args.get('team')
    return [e.model_dump(mode="json") for e in matrix_repo.list_all(team=team)], 200


@access_bp.route('/access/matrix', methods=['POST'])
@require_drivetrainer
def access_create_matrix_entry():
    body = request.get_json(force=True) or {}
    entry = matrix_repo.upsert(None, request.principal, **body)
    return entry.model_dump(mode="json"), 201


@access_bp.route('/access/matrix/<int:entry_id>', methods=['PUT'])
@require_drivetrainer
def access_update_matrix_entry(entry_id):
    body = request.get_json(force=True) or {}
    entry = matrix_repo.upsert(entry_id, request.principal, **body)
    return entry.model_dump(mode="json"), 200


@access_bp.route('/access/matrix/<int:entry_id>', methods=['DELETE'])
@require_drivetrainer
def access_delete_matrix_entry(entry_id):
    matrix_repo.deactivate(entry_id, request.principal)
    return '', 204
