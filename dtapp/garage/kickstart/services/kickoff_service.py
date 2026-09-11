import logging
from typing import Optional

from dtapp.garage.kickstart.clients import sendgrid_client
from dtapp.garage.kickstart import tasks as tasks_core
from dtapp.garage.core.config import settings
from dtapp.garage.core import constants, s3_client
from dtapp.garage.kickstart.enums import KickoffStatus
from dtapp.garage.core.errors import BadRequestError, NotFoundError
from dtapp.garage.kickstart.sqs import flush_outbox
from dtapp.garage.kickstart.repositories import kickoff_repo, consultant_repo
from dtapp.garage.assets import repo as asset_repo
from dtapp.garage.kickstart.schemas.kickoff import (
    EDITABLE_FIELDS,
    KickoffData,
    RegisterPayload,
    flatten_groups,
    lower_email,
    merge_and_validate_edit,
    order_form_filename,
)
from dtapp.garage.kickstart.kickoff_steps import run_register_steps, step_done
from dtapp.garage.kickstart import token as token_module
from dtapp.garage.kickstart.token import mint_token, form_url

logger = logging.getLogger(__name__)


def create_draft(data: dict, principal: str) -> str:
    """POST — create a new draft from a partial register form (NO side-effects). Returns koid."""
    return kickoff_repo.create_draft(data, principal)


def update_draft(koid: str, data: dict) -> bool:
    """PUT — autosave/update an existing draft. False if it's not an open draft anymore."""
    return kickoff_repo.update_draft(koid, data)


def is_draft(koid: str) -> bool:
    """True if `koid` is an unsubmitted DRAFT (creator-private) rather than a real kickoff. Lets the
    DELETE route branch draft-vs-kickoff without reaching into the repo (the flag gate applies to real
    kickoffs only)."""
    return kickoff_repo.get_draft(koid) is not None


def kickoff_exists(koid: str) -> bool:
    """True if `koid` is an existing, active (non-soft-deleted) real kickoff."""
    return kickoff_repo.get_kickoff(koid) is not None


def delete_draft(koid: str, principal: str) -> bool:
    """DELETE — discard the caller's own draft. False if not a draft / not owner / not found."""
    return kickoff_repo.delete_draft(koid, principal)


def delete_kickoff(koid: str) -> bool:
    """DELETE — soft-delete a real (submitted) kickoff (is_active → false). Record-only: no Linear/
    Slack/Monday cleanup. False if not found / already inactive. (Drafts go through delete_draft.)"""
    return kickoff_repo.soft_delete_kickoff(koid)


def submit_draft(koid: str, payload: RegisterPayload, principal: str) -> str | None:
    """Promote a draft to a real kickoff at status REGISTERING (is_draft → False), then enqueue the
    async register-ancillary job. Same fast-path as register_kickoff — no sync external work. Returns
    the koid, or None if koid isn't an existing draft."""
    # Order form is mandatory at submit — reject if none was uploaded against this draft (the asset is
    # registered with reference_id = koid during the wizard, and the koid is unchanged on submit).
    if not asset_repo.list_assets(koid, "order_form"):
        raise BadRequestError("An order form (PDF) must be attached before submitting.")
    data = payload.model_dump(mode="json")
    # AE email (server-set) passed TYPED to the repo, not poked into the blob dict; canonicalized = lowercase.
    koid = kickoff_repo.submit_draft_atomic(koid, data, registered_by=lower_email(principal or ""))
    if koid is None:
        return None
    flush_outbox(koid=koid)
    return koid


def update_fields(koid: str, changes: dict) -> dict:
    """Edit AE fields on a real (non-draft) kickoff. Record-only: NO downstream propagation.
    Whitelists keys, merges onto the stored register groups, re-validates via RegisterPayload,
    then persists. Raises NotFoundError (404) for a draft/unknown koid, BadRequestError (400) for
    a non-editable key, pydantic ValidationError (400) for an invalid value."""
    k = kickoff_repo.get_kickoff(koid)
    if k is None:
        raise NotFoundError(f"Kickoff {koid} not found")
    if not isinstance(changes, dict) or not changes:
        raise BadRequestError("No fields to update.")
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise BadRequestError("Not editable: " + ", ".join(sorted(unknown)))

    payload = merge_and_validate_edit(k.data, changes)  # ValidationError → 400
    validated = payload.model_dump(mode="json")  # {company_details, deal_details, sales_handoff}
    new_data = {**k.data, **validated}  # overwrite the 3 group keys; keep workflow keys (tenant_id, references, ...)
    kickoff_date_iso = (validated.get("deal_details") or {}).get("kickoff_date")
    kickoff_repo.update_kickoff_fields(koid, new_data, kickoff_date_iso)
    return {"koid": koid, "updated": sorted(changes.keys())}


def handle_register(message: dict):
    """Consume an Axle-queue {koid, type:"register"} message: run the register-ancillary work that
    used to run synchronously inside the register transaction — cleanup → Monday → Slack → Linear →
    mint token → intake email — then advance REGISTERING → AWAITING_CUSTOMER_RESPONSE.

    Effectively-once / resumable under SQS redelivery:
      1. run_register_steps is ref-guarded (Linear/Monday skip on a present ref; cleanup + Slack are
         idempotent); its refs are merged into data['references'] so a retry resumes cleanly.
      2. the token + intake email are guarded by the 'intake_email' completed-steps flag, so a
         redelivery never mints a second token or sends a duplicate email.
      3. status advances ONLY after both succeed, so a mid-flow failure leaves the row REGISTERING
         and a redelivery finishes the remaining work.
    Raises on any failure (after recording last_error) so Axle's SQS queue redelivers (bounded; no
    DLQ — a stuck REGISTERING kickoff surfaces via monitoring; recovery is a re-enqueue, which now
    resumes cleanly since every step is idempotent)."""
    koid = message.get("koid")
    if not koid:
        logger.warning("[kickstart] register message missing koid: %s", message)
        return

    kickoff = kickoff_repo.get_kickoff(koid)  # REGISTERING rows are is_draft=False → fetchable
    if kickoff is None:
        logger.warning("[kickstart] register for unknown koid %s — skipping", koid)
        return

    data = flatten_groups(kickoff.data)  # flat read view of the grouped blob for the steps + intake email
    kd = KickoffData.from_blob(kickoff.data)
    try:
        # 1. ancillary steps (cleanup → Linear → Monday → Slack); ref-guarded + idempotent. Persist
        #    the produced refs so a redelivery sees them and skips the non-idempotent creates.
        refs = run_register_steps(koid, data)
        kickoff_repo.merge_references(koid, refs)
        data = {**data, "references": {**kd.references, **refs}}
        # 2. token + intake email — guarded by a step flag so a retry never mints a 2nd token / sends
        #    a duplicate email. The intake email is no longer a sync gate; it runs here.
        if not step_done(data, "intake_email"):
            raw = mint_token(koid, kd.onboarding_contact_email)
            intake_link = form_url(raw)
            if settings.dryrun_unconfigured and not settings.sendgrid_api_key:
                logger.info("[kickstart] DRYRUN send_intake_email for %s — link=%s", koid, intake_link)
            else:
                sendgrid_client.send_intake_email(koid, data, intake_link)
            kickoff_repo.merge_references(koid, {"intake_link": intake_link})
            kickoff_repo.mark_step_done(koid, "intake_email")
    except Exception as e:  # noqa: BLE001 — record + re-raise so SQS redelivers (bounded; no DLQ)
        kickoff_repo.set_last_error(koid, e)
        logger.exception("[kickstart] register failed for %s", koid)
        raise
    # advance ONLY after all register work succeeded — a mid-flow failure leaves it REGISTERING.
    kickoff_repo.set_status_explicit(koid, KickoffStatus.AWAITING_CUSTOMER_RESPONSE)
    logger.info("[kickstart] %s register complete → AWAITING_CUSTOMER_RESPONSE", koid)


def _order_form_for(koid: str, company_name: Optional[str] = None, data: Optional[dict] = None):
    """Slim embedded order-form asset for a koid (no s3_key); presignedUrl is a 30-min GET. None if
    none uploaded. Used by BOTH the detail payload and the draft-resume payload so the FE never needs
    a separate /garage/assets fetch. Surfaces the canonical name (over the AE's arbitrary upload name);
    pass company_name when the caller already parsed it (detail path), else it's read from data."""
    rows = asset_repo.list_assets(koid, "order_form")
    if not rows:
        return None
    a = rows[-1]  # most recent (list_assets is created_at ASC)
    if company_name is None and data is not None:
        company_name = KickoffData.from_blob(data).company_name
    name = order_form_filename(company_name, a.filename)
    return {
        'fileName': name,
        'fileType': a.content_type,
        'fileSize': a.size_bytes,
        'presignedUrl': s3_client.presign_get(a.s3_key, download_as=name, expires=1800),
    }


def serialize_detail(k) -> dict:
    """Build the detail-page payload: flattened record + references + derived progress + links."""
    # Flatten the grouped JSONB blob into the flat record the FE detail reads; then drop the internal
    # workflow keys (below) and layer on derived/column fields. kd is a typed READ view of the same blob.
    kd = KickoffData.from_blob(k.data)
    record = flatten_groups(k.data)  # FE detail reads flat keys; the blob is stored grouped
    # Batch-resolve the (up to 3) consultant ids → email in one pass to avoid N+1 sessions.
    role_ids = {cid for cid in (k.solution_consultant_id, k.analyst_id, k.pod_lead_id) if cid is not None}
    consultant_map = {cid: c.email for cid in role_ids if (c := consultant_repo.get_consultant(cid))}
    record.update({
        'koid': k.koid,
        'status': k.status,
        'registered_by': k.registered_by,
        'slack_channel_config': k.slack_channel_config,
        'created_at': k.created_at.isoformat() if k.created_at else None,
        'updated_at': k.updated_at.isoformat() if k.updated_at else None,
        'submitted_at': k.submitted_at.isoformat() if k.submitted_at else None,
        'time_zone': kd.time_zone,
        'domain_name': kd.domain_name,
        'solution_consultant': consultant_map.get(k.solution_consultant_id),
        'analyst': consultant_map.get(k.analyst_id),
        'pod_lead': consultant_map.get(k.pod_lead_id),
        'tenant_id': k.tenant_id or kd.poc_tenant_id,
    })
    # Trim internal workflow keys + the references duplicate (references is returned once, top-level).
    for _internal in ("completed_steps", "task_errors", "_assign_removed", "references"):
        record.pop(_internal, None)
    # References = produced links/ids (new data["references"] + legacy top-level keys).
    stored_refs = kd.references
    # Stringer sales-context brief (markdown) — its own field, not a reference link.
    record['tenant_context'] = stored_refs.get('tenant_context')
    _chan = k.slack_channel_config or {}
    slack_channel_url = (
        f"{constants.SLACK_WORKSPACE_URL}/archives/{_chan['channel_id']}"
        if _chan.get('channel_id') else None
    )
    references = {
        'monday_workspace_url': stored_refs.get('monday_workspace_url'),
        'gtm_announce': stored_refs.get('gtm_announce'),
        'kickoff_linear_url': stored_refs.get('kickoff_linear_url') or kd.linear_url,
        'kickoff_linear_id': stored_refs.get('kickoff_linear_id'),
        'connector_linear_urls': stored_refs.get('connector_linear_urls'),
        'academy_linear_url': stored_refs.get('academy_linear_url'),
        'heimdall_linear_url': stored_refs.get('heimdall_linear_url'),
        'intake_link': stored_refs.get('intake_link') or kd.intake_link,
        'tenant_subdomain': kd.domain_name,
        'slack_channel_url': slack_channel_url,
    }
    # Progress = derived 6-stage timeline + per-task status (no stored ledger).
    progress = tasks_core.compute_stages({
        'status': getattr(k.status, 'value', k.status),
        'created_at': record['created_at'],
        'tenant_id': k.tenant_id,
        'provisioning_done_at': k.provisioning_done_at.isoformat() if k.provisioning_done_at else None,
        'poc_tenant_id': kd.poc_tenant_id,
        'connectors': kd.connectors,
        'academy_users': kd.academy_users,
        # Slack channel signals for honest channel-task derivation (create/rename/add).
        'channel_id': (k.slack_channel_config or {}).get('channel_id'),
        'channel_name': (k.slack_channel_config or {}).get('name'),
        'has_consultant': k.solution_consultant_id is not None,
        # presence bool, not the multi-KB brief (that's on record['tenant_context']) — avoids double-serialising.
        'references': {**references, 'tenant_context': bool(stored_refs.get('tenant_context'))},
        'task_errors': kd.task_errors,
        # drives the real/dry-run chip per integration
        'live': {'linear': bool(settings.linear_api_key), 'monday': bool(settings.monday_api_key),
                 'slack': bool(settings.kickstart_slack_bot_token)},
    }, k.status_log or [])
    # links kept for the existing Results-&-links block (back-compat).
    links = {
        'intake_link': references['intake_link'],
        'linear_url': references['kickoff_linear_url'],
        'monday_workspace_url': references['monday_workspace_url'],
        'tenant_subdomain': references['tenant_subdomain'],
    }
    return {
        'record': record, 'links': links, 'references': references,
        'progress': progress,
        # intake link state ({state}: none/valid/submitted) — drives the AE intake-link panel.
        'intake': token_module.intake_state(k.koid),
        'order_form': _order_form_for(k.koid, company_name=kd.company_name),
    }


def get_detail(koid: str, principal: str) -> dict:
    """Resolve the GET-detail payload for a koid. A draft → its raw form state for resume
    (creator-private; NotFoundError to a non-owner). A real kickoff → the detail payload.
    Raises NotFoundError if neither a draft (the caller owns) nor a real kickoff exists."""
    draft = kickoff_repo.get_draft(koid)
    if draft is not None:
        if draft.registered_by != principal:
            raise NotFoundError(f"Kickoff {koid} not found")
        return {'is_draft': True, 'koid': draft.koid, 'data': draft.data,
                'updated_at': draft.updated_at.isoformat() if draft.updated_at else None,
                'order_form': _order_form_for(draft.koid, data=draft.data)}
    k = kickoff_repo.get_kickoff(koid)
    if k is None:
        raise NotFoundError(f"Kickoff {koid} not found")
    return {**serialize_detail(k), 'is_draft': False}


def list_page(page: int, limit: int, q, status, created_by, date_from, date_to,
              sort, order, is_draft, kickoff_date_from=None, kickoff_date_to=None,
              consultant=None, assigned_to_me=False, principal=None) -> dict:
    """Paginated kickoff list page: query the repo + batch-resolve solution_consultant emails for the
    items. Returns {total, page, limit, items[]}. The consultant filter is an explicit `consultant`
    email (wins) or `assigned_to_me` = the caller (`principal`), resolved to a consultant id here
    (-1 → empty when that email isn't a consultant; None → no filter). `created_by` is the
    already-scoped creator filter (forced to the caller for drafts)."""
    # Consultant filter → id: an explicit email wins, else "assigned to me" = the caller.
    consultant_email = lower_email(consultant or (principal if assigned_to_me else '') or '')
    consultant_id = None
    if consultant_email:
        c = consultant_repo.get_consultant_by_email(consultant_email)
        consultant_id = c.id if c else -1
    rows, total = kickoff_repo.list_kickoffs(
        created_by=created_by,
        date_from=date_from,
        date_to=date_to,
        kickoff_date_from=kickoff_date_from,
        kickoff_date_to=kickoff_date_to,
        sort=sort,
        order=order,
        status=status,
        skip=(page - 1) * limit,
        limit=limit,
        q=q,
        consultant_id=consultant_id,
        is_draft=is_draft,
    )
    # Batch-resolve solution_consultant_id → email to avoid N+1 queries.
    consultant_ids = {r.solution_consultant_id for r in rows if r.solution_consultant_id is not None}
    consultant_map = {cid: c.email for cid in consultant_ids if (c := consultant_repo.get_consultant(cid))}
    def _item(r):
        # company_name + poc_tenant_id live under company_details (grouped blob); fall back to a
        # top-level key for legacy already-flat blobs. Targeted read — cheaper than flatten_groups per row.
        cd = r.data.get('company_details') or {}
        return {
            'koid': r.koid,
            'company_name': cd.get('company_name') or r.data.get('company_name'),
            'status': r.status,
            'registered_by': r.registered_by,
            'is_draft': r.is_draft,
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
            'submitted_at': r.submitted_at.isoformat() if r.submitted_at else None,
            'kickoff_date': r.kickoff_date.isoformat() if r.kickoff_date else None,
            'solution_consultant': consultant_map.get(r.solution_consultant_id),
            'tenant_id': r.tenant_id,               # real (provisioned) tenant; null until provisioned
            'poc_tenant_id': cd.get('poc_tenant_id') or r.data.get('poc_tenant_id'),  # POC — FE shows "POC <id>"
        }
    items = [_item(r) for r in rows]
    return {'total': total, 'page': page, 'limit': limit, 'items': items}
