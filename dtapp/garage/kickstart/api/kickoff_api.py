from flask import Blueprint, request

from dtapp.garage.core import flags
from dtapp.garage.core.auth import require_drivetrainer
from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import KICKSTART_DELETE_FLAG
from dtapp.garage.core.errors import NotFoundError, register_error_handlers
from dtapp.garage.kickstart.schemas.kickoff import RegisterPayload, KickoffCreated, IntakeSubmission, AssignPayload, flatten_groups
from dtapp.garage.kickstart.services import kickoff_service, intake_service, assign_service, consultant_service
from dtapp.garage.kickstart.repositories import kickoff_repo, consultant_repo
from dtapp.garage.kickstart import token as token_module
# (no queue import needed in the API layer in v2)

kickstart_bp = Blueprint("kickstart", __name__)
register_error_handlers(kickstart_bp)


def parse_body(model, force=False):
    """Validate the request JSON into `model`. A pydantic ValidationError propagates to the
    registered handler → {"error": "validation_failed", "detail": [...]}, 400."""
    return model(**(request.get_json(force=force) or {}))


@kickstart_bp.route('/garage/kickstart/kickoff/list', methods=['POST'])
@require_drivetrainer
def kickstart_list_kickoffs():
    """Paginated kickoff list. Body (all optional): {page, limit, status, created_by, date_from,
    date_to, kickoff_date_from, kickoff_date_to, sort, order, q, consultant, assigned_to_me, is_draft} →
    {total, page, limit, items[]}.
    `is_draft:true` → the caller's OWN drafts (creator-private; created_by is forced to the caller)."""
    body = request.get_json(silent=True) or {}
    page = max(1, int(body.get('page') or 1))
    limit = min(200, max(1, int(body.get('limit') or 20)))
    q = (body.get('q') or '').strip() or None
    is_draft = bool(body.get('is_draft'))
    # Drafts are creator-private → force-scope to the caller (ignore any created_by). Real kickoffs are
    # visible to every drivetrainer and accept an optional created_by filter.
    created_by = request.principal if is_draft else body.get('created_by')
    # Consultant filter (email or "assigned to me") is resolved to an id in the service layer.
    return kickoff_service.list_page(
        page=page,
        limit=limit,
        q=q,
        status=body.get('status'),
        created_by=created_by,
        date_from=body.get('date_from'),
        date_to=body.get('date_to'),
        kickoff_date_from=body.get('kickoff_date_from'),
        kickoff_date_to=body.get('kickoff_date_to'),
        sort=body.get('sort', 'created_at'),
        order=body.get('order', 'desc'),
        consultant=body.get('consultant'),
        assigned_to_me=bool(body.get('assigned_to_me')),
        principal=request.principal,
        is_draft=is_draft,
    ), 200


@kickstart_bp.route('/garage/kickstart/kickoff/meta', methods=['GET'])
@require_drivetrainer
def kickstart_kickoff_meta():
    """Distinct filter values for the ops-list dropdowns — AE emails (distinct registered_by across
    real kickoffs) + consultant emails (active roster, so even unassigned consultants are filterable).
    One cheap indexed query each; scales O(distinct) regardless of kickoff count. (Static path — ranks
    above /kickoff/<koid> in Werkzeug, so no collision.)"""
    return {
        "ae": kickoff_repo.distinct_registered_by(),
        "consultants": [c.email for c in consultant_repo.list_consultants()],
    }, 200


@kickstart_bp.route('/garage/kickstart/kickoff', methods=['POST'])
@require_drivetrainer
def kickstart_create_kickoff():
    """Every kickoff is born as a DRAFT — there is no direct register. Body: {data}. No validation,
    no side-effects; the FE autosaves via PUT and submits via PUT {submit:true}. Returns the koid."""
    body = request.get_json(force=True) or {}
    koid = kickoff_service.create_draft(body.get('data') or {}, request.principal)
    return {'koid': koid, 'is_draft': True}, 201


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>', methods=['PUT'])
@require_drivetrainer
def kickstart_update_kickoff(koid):
    """PUT a draft — AUTOSAVE only (partial replace; NO validation, NO side-effects). Entertained ONLY
    while it's a draft (404 once submitted). Submit is a separate action → POST /garage/kickstart/kickoff/<koid>/submit."""
    body = request.get_json(force=True) or {}
    if not kickoff_service.update_draft(koid, body.get('data') or {}):
        raise NotFoundError(f"Kickoff {koid} not found or already submitted")
    return {'koid': koid, 'is_draft': True}, 200


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>/submit', methods=['POST'])
@require_drivetrainer
def kickstart_submit_kickoff(koid):
    """Submit a draft — full-validate the RegisterPayload (400 on invalid) + run the all-or-nothing
    register (flips is_draft→false, fires the intake email / demo cleanup). Body: {data}. 404 if not a
    draft / already submitted. Separate from PUT so an autosave can never accidentally fire side-effects."""
    body = request.get_json(force=True) or {}
    payload = RegisterPayload(**(body.get('data') or {}))  # full validation; ValidationError → 400
    result = kickoff_service.submit_draft(koid, payload, request.principal)
    if result is None:
        raise NotFoundError(f"Kickoff {koid} not found or already submitted")
    return KickoffCreated(koid=result).model_dump(), 200


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>', methods=['DELETE'])
@require_drivetrainer
def kickstart_delete_kickoff(koid):
    """DELETE — discard a DRAFT (hard, owner-guarded) or soft-delete a real KICKOFF (is_active→false).
    A real-kickoff delete is gated by the kickoff-delete LD flag (Paaras-only) and is record-only: it
    does NOT touch Linear/Slack/Monday. 404 if not found / (draft) not owner; 403 if not flag-permitted."""
    if kickoff_service.is_draft(koid):
        # Draft — creator-private hard delete (existing behaviour; no flag gate).
        if not kickoff_service.delete_draft(koid, request.principal):
            raise NotFoundError(f"Draft {koid} not found")
    elif not kickoff_service.kickoff_exists(koid):
        # Unknown koid → 404 before the flag gate (an unflagged caller never sees 403 for a missing koid).
        raise NotFoundError(f"Kickoff {koid} not found")
    else:
        # Real kickoff — strict flag gate (Paaras): a missing/unknown flag → 500, never fail-open on delete.
        flags.require_access_strict(KICKSTART_DELETE_FLAG, request.principal)
        if not kickoff_service.delete_kickoff(koid):
            raise NotFoundError(f"Kickoff {koid} not found")
    return {'status': 'deleted', 'koid': koid}, 200


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>', methods=['GET'])
@require_drivetrainer
def kickstart_get_kickoff(koid):
    # A draft → its raw form state for resume (creator-private). A real kickoff → the detail payload.
    return kickoff_service.get_detail(koid, request.principal), 200


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>', methods=['PATCH'])
@require_drivetrainer
def kickstart_edit_kickoff(koid):
    """PATCH editable AE fields of a real (non-draft) kickoff. Body: a flat object of changed fields
    (e.g. {"time_zone": "...", "connectors": [...]}, or {"handoff_checklist": {...}}). Record-only:
    NO downstream propagation to Linear/Slack/Monday/tenant. 404 for a draft/unknown koid; 400 for a
    non-editable key or invalid value."""
    changes = request.get_json(force=True) or {}
    result = kickoff_service.update_fields(koid, changes)
    return {'status': 'updated', **result}, 200


@kickstart_bp.route('/garage/kickstart/kickoff/<koid>/assign', methods=['PATCH'])
@require_drivetrainer
def kickstart_assign_consultant(koid):
    # Server-side LD gate is the REAL boundary (the FE button is UX-only; never rely on FE hiding).
    # Same flag as the FE; DENIED → 403, LD oncall → 502 (fail-closed); local → not enforced.
    flags.require_access("kickstart-assign-view", request.principal)
    payload = parse_body(AssignPayload)
    # raises NotFoundError(404) if koid unknown
    assign_service.assign_consultant(koid, payload.consultant, payload.analyst, payload.pod_lead)
    return {'status': 'assigned', 'koid': koid}, 200


@kickstart_bp.route('/garage/kickstart/consultants', methods=['GET'])
@require_drivetrainer
def kickstart_list_consultants():
    rows = consultant_repo.list_consultants(q=request.args.get('q'))
    return [{'id': r.id, 'name': r.name, 'email': r.email} for r in rows], 200


@kickstart_bp.route('/garage/kickstart/consultants/sync', methods=['POST'])
@require_drivetrainer
def kickstart_sync_consultants():
    return consultant_service.sync_consultants(), 200


# Public endpoints — token IS the auth; no @require_drivetrainer
@kickstart_bp.route('/garage/kickstart/intake/<token>', methods=['GET'])
def kickstart_intake_validate(token):
    state, koid = token_module.token_state(token)
    if state == 'invalid':
        return {'state': 'invalid'}, 404
    k = kickoff_repo.get_kickoff(koid)
    flat = flatten_groups(k.data)
    # onboarding_contact_name = the contact the intake link was addressed to; the form greets this person.
    # requires_eu_hosting (AE-set at register) → the intake form renders the .eu.example.com suffix.
    return {'state': state, 'company_name': flat.get('company_name'),
            'onboarding_contact_name': flat.get('onboarding_contact_name'),
            'requires_eu_hosting': flat.get('requires_eu_hosting'), 'koid': koid}, 200


@kickstart_bp.route('/garage/kickstart/intake/<token>', methods=['POST'])
def kickstart_intake_submit(token):
    submission = IntakeSubmission(**(request.get_json(force=True) or {}).get('data') or {})
    koid = intake_service.submit_intake(token, submission)  # raises ConflictError(409) if already used/submitted
    return {'status': 'received', 'koid': koid}, 200  # accept-fast; tenant creation runs async in Axle's provisioning consumer


# Assets moved to their own module (dtapp/assets) — see assets_bp.


# ── API docs (non-prod only) ────────────────────────────────────────────────
# OpenAPI spec + Swagger UI for every /garage/kickstart/* endpoint. Served under the Garage→Kickstart namespace
# (/garage/kickstart/*) — the docs are new + dev-only (no FE contract), so they follow the platform
# hierarchy even though the documented business routes stay /garage/kickstart/* (FE/data contract). Gated to
# non-prod (settings.is_prod) so prod never exposes them — and 404s there (not 403), revealing
# nothing. No @require_drivetrainer: these are static docs with no data; "Try it out" calls still hit
# the real authed endpoints (use the Authorize button to paste an Okta token).

@kickstart_bp.route('/garage/kickstart/openapi.json', methods=['GET'])
def kickstart_openapi_spec():
    if settings.is_prod:
        raise NotFoundError("Not found")
    from dtapp.garage.kickstart.api.openapi import build_spec
    return build_spec(), 200


@kickstart_bp.route('/garage/kickstart/docs', methods=['GET'])
def kickstart_docs():
    if settings.is_prod:
        raise NotFoundError("Not found")
    from dtapp.garage.kickstart.api.openapi import swagger_ui_html
    return swagger_ui_html('/garage/kickstart/openapi.json'), 200, {'Content-Type': 'text/html; charset=utf-8'}


# Audit/status_log is served inline by GET /garage/kickstart/kickoff/<koid> (_serialize_kickoff → 'status_log');
# no separate /audit endpoint — it was a redundant second fetch of the same column.
