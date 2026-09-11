"""Dev-only OpenAPI 3 spec + Swagger UI for the Kickstart API.

Why hand-authored paths (not a framework): the routes are plain Flask + pydantic, so we
get the best request/response docs by describing each operation explicitly while pulling the
*request body* schemas straight from the pydantic models (single source of truth — they can't
drift). `undocumented_operations()` cross-checks the spec against the live blueprint so a new
route can never silently go undocumented.

Served by two routes in kickoff_api.py, gated to non-prod (see settings.is_prod). They live under the
Garage→Kickstart namespace (the docs are new/dev-only; the documented business routes stay /garage/kickstart/*):
  GET /garage/kickstart/openapi.json  → this document
  GET /garage/kickstart/docs          → Swagger UI (CDN) pointed at it
"""
from functools import lru_cache

from dtapp.garage.kickstart.schemas.kickoff import (
    AssignPayload,
    IntakeSubmission,
    RegisterPayload,
)

# Request models whose JSON Schema is generated from pydantic (the source of truth).
_REQUEST_MODELS = [RegisterPayload, IntakeSubmission, AssignPayload]

_BEARER = [{"bearerAuth": []}]   # drivetrainer Okta JWT
_PUBLIC = []                     # token-in-path is the auth (customer intake)

_REF = "#/components/schemas/{model}"


def _request_schemas() -> dict:
    """Pull request-body schemas (+ their nested models/enums) from pydantic, with $refs rewritten
    to point at components/schemas. Deduped by model name across all request models."""
    out: dict = {}
    for model in _REQUEST_MODELS:
        schema = model.model_json_schema(ref_template=_REF)
        for name, definition in schema.pop("$defs", {}).items():
            out.setdefault(name, definition)
        out[model.__name__] = schema
    return out


def _str(desc: str, **extra) -> dict:
    return {"type": "string", "description": desc, **extra}


def _response_schemas() -> dict:
    """Response + error envelope schemas, authored to match the api-layer serializers exactly."""
    nullable_str = {"type": "string", "nullable": True}
    return {
        "ErrorEnvelope": {
            "type": "object",
            "description": "Uniform error envelope (see core/errors.py).",
            "properties": {
                "success": {"type": "boolean", "example": False},
                "status_code": {"type": "integer", "example": 404},
                "code": {"type": "string", "description": "Stable machine-readable code for FE branching.", "example": "not_found"},
                "timestamp": {"type": "string", "format": "date-time"},
                "path": {"type": "string"},
                "message": {
                    "description": "Human-readable message, or pydantic error list on validation_failed.",
                    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}],
                },
            },
            "required": ["success", "status_code", "message"],
        },
        "KickoffListItem": {
            "type": "object",
            "description": "One row of POST /garage/kickstart/kickoff/list (kickoffs, or the caller's drafts when is_draft:true).",
            "properties": {
                "koid": {"type": "string"},
                "company_name": nullable_str,
                "status": {"type": "string", "example": "provisioning"},
                "registered_by": nullable_str,
                "is_draft": {"type": "boolean"},
                "created_at": {"type": "string", "format": "date-time", "nullable": True},
                "updated_at": {"type": "string", "format": "date-time", "nullable": True},
                "submitted_at": {"type": "string", "format": "date-time", "nullable": True},
                "solution_consultant": {**nullable_str, "description": "Resolved consultant email, or null if unassigned."},
                "tenant_id": {**nullable_str, "description": "Real tenant id, falling back to poc_tenant_id."},
            },
        },
        "DraftCreated": {
            "type": "object",
            "properties": {"koid": {"type": "string"}, "is_draft": {"type": "boolean", "example": True}},
            "required": ["koid", "is_draft"],
        },
        "DraftState": {
            "type": "object",
            "description": "GET /garage/kickstart/kickoff/{koid} when {koid} is a draft — raw form state for resume.",
            "properties": {
                "is_draft": {"type": "boolean", "example": True},
                "koid": {"type": "string"},
                "data": {"type": "object", "description": "Partial register-form state.", "additionalProperties": True},
                "updated_at": {"type": "string", "format": "date-time", "nullable": True},
            },
        },
        "KickoffCreated": {
            "type": "object",
            "properties": {"koid": {"type": "string"}},
            "required": ["koid"],
        },
        "References": {
            "type": "object",
            "description": "Produced links/ids per kickoff (Results & links block).",
            "properties": {
                "monday_workspace_url": nullable_str,
                "gtm_announce": nullable_str,
                "kickoff_linear_url": nullable_str,
                "kickoff_linear_id": nullable_str,
                "connector_linear_urls": {"type": "object", "nullable": True, "additionalProperties": {"type": "string"}},
                "academy_linear_url": nullable_str,
                "heimdall_linear_url": nullable_str,
                "intake_link": nullable_str,
                "tenant_subdomain": nullable_str,
                "slack_channel_url": nullable_str,
            },
        },
        "KickoffDetail": {
            "type": "object",
            "description": "GET /garage/kickstart/kickoff/{koid} for a real (submitted) kickoff — the detail-page payload.",
            "properties": {
                "is_draft": {"type": "boolean", "example": False},
                "record": {
                    "type": "object",
                    "description": "Flattened kickoff (JSONB data + explicit columns). Common keys: "
                    "koid, company_name, status, registered_by, tenant_id, time_zone, domain_name, "
                    "solution_consultant, analyst, pod_lead, created_at, submitted_at.",
                    "additionalProperties": True,
                },
                "links": {
                    "type": "object",
                    "description": "Back-compat subset of references.",
                    "additionalProperties": True,
                },
                "references": {"$ref": "#/components/schemas/References"},
                "progress": {
                    "type": "object",
                    "description": "Derived 6-stage timeline + per-task status (no stored ledger).",
                    "additionalProperties": True,
                },
                "intake": {
                    "type": "object",
                    "description": "Intake-link state {state}: none/valid/submitted.",
                    "additionalProperties": True,
                },
            },
        },
        "KickoffPage": {
            "type": "object",
            "description": "One page of POST /garage/kickstart/kickoff/list (Forge page+limit pagination).",
            "properties": {
                "total": {"type": "integer", "description": "Total matching kickoffs (all pages)."},
                "page": {"type": "integer"},
                "limit": {"type": "integer"},
                "items": {"type": "array", "items": {"$ref": "#/components/schemas/KickoffListItem"}},
            },
            "required": ["total", "page", "limit", "items"],
        },
        "Consultant": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "email": {"type": "string", "format": "email"},
            },
            "required": ["id", "name", "email"],
        },
        "SyncResult": {
            "type": "object",
            "description": "Outcome of a consultant-roster sync.",
            "properties": {
                "synced": {"type": "integer", "description": "Active CS people upserted from the sheet."},
                "deactivated": {"type": "integer", "description": "Consultants soft-deleted (no longer in the roster)."},
                "skipped": {**nullable_str, "description": "Set when the reconcile was skipped (e.g. sheet unreachable / empty)."},
                "source": {"type": "string", "example": "gsheet"},
            },
        },
        "Asset": {
            "type": "object",
            "description": "An uploaded asset (e.g. order form PDF).",
            "properties": {
                "id": {"type": "integer"},
                "purpose": {"type": "string", "example": "order_form"},
                "reference_id": {"type": "string", "description": "Owning kickoff koid (or other ref)."},
                "s3_key": {"type": "string"},
                "filename": nullable_str,
                "content_type": nullable_str,
                "size_bytes": {"type": "integer", "nullable": True},
                "created_at": {"type": "string", "format": "date-time", "nullable": True},
            },
        },
        "PresignResponse": {
            "type": "object",
            "properties": {
                "s3_key": {"type": "string"},
                "upload_url": {"type": "string", "description": "Presigned S3 PUT URL (Content-Type bound)."},
                "expires_in": {"type": "integer", "example": 600},
                "max_bytes": {"type": "integer", "description": "Per-purpose size cap; reject oversized client-side.", "example": 10485760},
            },
        },
        "IntakeValidate": {
            "type": "object",
            "description": "GET /garage/kickstart/intake/{token} — link state + who the form greets.",
            "properties": {
                "state": {"type": "string", "example": "valid", "enum": ["valid", "submitted", "invalid"]},
                "company_name": nullable_str,
                "onboarding_contact_name": nullable_str,
                "koid": nullable_str,
            },
        },
        "StatusAck": {
            "type": "object",
            "description": "Generic action acknowledgement.",
            "properties": {"status": {"type": "string"}, "koid": {"type": "string"}},
        },
    }


# ── path parameters (reused) ─────────────────────────────────────────────────
def _path_param(name: str, desc: str, kind: str = "string") -> dict:
    return {"name": name, "in": "path", "required": True, "schema": {"type": kind}, "description": desc}


_KOID = _path_param("koid", "Kickoff id.")


def _json_body(ref: str, required: bool = True) -> dict:
    return {"required": required, "content": {"application/json": {"schema": {"$ref": ref}}}}


def _inline_body(schema: dict, required: bool = True) -> dict:
    return {"required": required, "content": {"application/json": {"schema": schema}}}


def _envelope_schema(data_schema: dict) -> dict:
    """Wrap a payload schema in the uniform success envelope (matches errors.py after_request)."""
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "example": True},
            "path": {"type": "string"},
            "status_code": {"type": "integer", "example": 200},
            "message": {"type": "string", "example": "OK"},
            "data": data_schema,
        },
        "required": ["success", "message", "data"],
    }


def _resp(desc: str, ref: str | None = None, is_array: bool = False) -> dict:
    """A 2xx response: the data schema wrapped in the success envelope."""
    if ref is None:
        data_schema: dict = {"type": "object", "nullable": True}
    elif is_array:
        data_schema = {"type": "array", "items": {"$ref": ref}}
    else:
        data_schema = {"$ref": ref}
    return {"description": desc, "content": {"application/json": {"schema": _envelope_schema(data_schema)}}}


def _resp_inline(desc: str, data_schema: dict) -> dict:
    """A 2xx response whose data is an inline (non-$ref) schema, wrapped in the success envelope."""
    return {"description": desc, "content": {"application/json": {"schema": _envelope_schema(data_schema)}}}


_ERR = "#/components/schemas/ErrorEnvelope"


def _err(desc: str) -> dict:
    return {"description": desc, "content": {"application/json": {"schema": {"$ref": _ERR}}}}


def _build_paths() -> dict:
    """Every documented operation, keyed by OpenAPI path ({param} form) then lowercase method."""
    return {
        "/garage/kickstart/kickoff": {
            "post": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER,
                "summary": "Create a draft kickoff",
                "description": "Every kickoff is born a draft — no direct register. No validation/side-effects; the FE autosaves via PUT and submits via POST /garage/kickstart/kickoff/{koid}/submit.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {"data": {"type": "object", "additionalProperties": True, "description": "Partial register-form state."}},
                }),
                "responses": {"201": _resp("Draft created.", "#/components/schemas/DraftCreated"), "401": _err("Unauthorized."), "403": _err("Forbidden.")},
            },
        },
        "/garage/kickstart/kickoff/list": {
            "post": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER,
                "summary": "List kickoffs (paginated)",
                "description": "Server-side paginated ops list (real kickoffs only). All body fields optional.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {
                        "page": {"type": "integer", "default": 1, "minimum": 1},
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                        "q": _str("Free-text search by customer name (ilike)."),
                        "assigned_to_me": {"type": "boolean", "description": "Only kickoffs where the caller is the consultant."},
                        "is_draft": {"type": "boolean", "description": "true → the caller's OWN drafts (creator-private; created_by forced to caller)."},
                        "status": _str("Filter by KickoffStatus."),
                        "created_by": _str("Filter by registering AE email."),
                        "date_from": {"type": "string", "format": "date-time"},
                        "date_to": {"type": "string", "format": "date-time"},
                        "kickoff_date_from": {"type": "string", "format": "date"},
                        "kickoff_date_to": {"type": "string", "format": "date"},
                        "sort": {"type": "string", "default": "created_at"},
                        "order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                    },
                }, required=False),
                "responses": {
                    "200": _resp("One page of kickoffs.", "#/components/schemas/KickoffPage"),
                    "401": _err("Unauthorized."), "403": _err("Forbidden."),
                },
            },
        },
        "/garage/kickstart/kickoff/{koid}": {
            "get": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Get a kickoff (draft state or detail payload)",
                "description": "A draft koid → its raw form state (creator-private). A real kickoff → the detail payload.",
                "responses": {
                    "200": _resp_inline("Draft state or kickoff detail.", {"oneOf": [
                        {"$ref": "#/components/schemas/KickoffDetail"},
                        {"$ref": "#/components/schemas/DraftState"},
                    ]}),
                    "404": _err("Not found (or draft owned by someone else)."),
                },
            },
            "put": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Autosave a draft",
                "description": "Partial-replace autosave (NO validation, NO side-effects). Entertained only while a draft "
                "(404 once submitted). Submit is a separate action → POST /garage/kickstart/kickoff/{koid}/submit.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {"data": {"type": "object", "additionalProperties": True}},
                }),
                "responses": {
                    "200": _resp("Autosaved.", "#/components/schemas/DraftCreated"),
                    "404": _err("Not found or already submitted."),
                },
            },
            "delete": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Discard a draft (drafts-only, owner-guarded)",
                "description": "404 on a real kickoff / non-owner / not found, so a submitted kickoff can never be deleted here.",
                "responses": {"200": _resp("Deleted.", "#/components/schemas/StatusAck"), "404": _err("Draft not found.")},
            },
            "patch": {
                "tags": ["Lifecycle & assignment"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Edit AE fields of a real kickoff (post-register)",
                "description": "PATCH a flat object of changed editable fields (e.g. {time_zone, connectors} or "
                "{handoff_checklist}). Record-only — NO downstream propagation. Non-editable keys / invalid values → 400; "
                "a draft/unknown koid → 404.",
                "requestBody": _inline_body({
                    "type": "object",
                    "description": "Flat map of changed editable fields (whitelisted server-side against EDITABLE_FIELDS).",
                    "additionalProperties": True,
                }),
                "responses": {
                    "200": _resp("Updated.", "#/components/schemas/StatusAck"),
                    "400": _err("Non-editable key or validation failed."), "404": _err("Kickoff not found."),
                },
            },
        },
        "/garage/kickstart/kickoff/{koid}/submit": {
            "post": {
                "tags": ["Kickoffs & drafts"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Submit a draft (full-validate + register)",
                "description": "Full-validate against RegisterPayload + run the all-or-nothing register (fires the "
                "intake email / demo cleanup). 404 if not a draft / already submitted.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {"data": {"type": "object", "additionalProperties": True, "description": "Must satisfy RegisterPayload."}},
                }),
                "responses": {
                    "200": _resp("Registered.", "#/components/schemas/KickoffCreated"),
                    "400": _err("Validation failed."), "404": _err("Not found or already submitted."),
                },
            },
        },
        "/garage/kickstart/kickoff/{koid}/assign": {
            "patch": {
                "tags": ["Lifecycle & assignment"], "security": _BEARER, "parameters": [_KOID],
                "summary": "Assign consultant (+ optional analyst / pod lead)",
                "description": "Server-side LD gate (kickstart-assign-view) is the real boundary. Roles must not overlap.",
                "requestBody": _json_body("#/components/schemas/AssignPayload"),
                "responses": {
                    "200": _resp("Assigned.", "#/components/schemas/StatusAck"),
                    "400": _err("Validation failed."), "403": _err("Access denied (LD)."),
                    "404": _err("Kickoff not found."), "409": _err("Role overlap."), "502": _err("LD oncall (fail-closed)."),
                },
            },
        },
        "/garage/kickstart/consultants": {
            "get": {
                "tags": ["Consultants"], "security": _BEARER,
                "summary": "List consultants",
                "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Name/email substring filter."}],
                "responses": {"200": _resp("Consultants.", "#/components/schemas/Consultant", is_array=True)},
            },
        },
        "/garage/kickstart/consultants/sync": {
            "post": {
                "tags": ["Consultants"], "security": _BEARER,
                "summary": "Sync the consultant roster from Google Sheet (manual trigger)",
                "description": "Re-pulls the Customer-Success roster from the source-of-truth Google Sheet and reconciles "
                "(upsert current CS people, soft-delete those who left). Runs daily via CRON (2 PM IST); this is the on-demand "
                "trigger surfaced in the assign dialog when a consultant isn't found.",
                "responses": {"200": _resp("Sync result.", "#/components/schemas/SyncResult")},
            },
        },
        "/garage/kickstart/intake/{token}": {
            "get": {
                "tags": ["Customer intake (public)"], "security": _PUBLIC,
                "parameters": [_path_param("token", "Single-use intake token (the auth).")],
                "summary": "Validate an intake link",
                "description": "Public — the token IS the auth. Returns link state + who the form greets.",
                "responses": {"200": _resp("Link state.", "#/components/schemas/IntakeValidate"), "404": _err("Invalid token ({state:invalid}).")},
            },
            "post": {
                "tags": ["Customer intake (public)"], "security": _PUBLIC,
                "parameters": [_path_param("token", "Single-use intake token (the auth).")],
                "summary": "Submit the customer intake form",
                "description": "Public. Accept-fast — tenant creation runs async in Axle's provisioning consumer.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {"data": {"$ref": "#/components/schemas/IntakeSubmission"}},
                    "required": ["data"],
                }),
                "responses": {
                    "200": _resp("Received.", "#/components/schemas/StatusAck"),
                    "400": _err("Validation failed."), "409": _err("Token already used/submitted."),
                },
            },
        },
        "/garage/assets/presign": {
            "post": {
                "tags": ["Assets"], "security": _BEARER,
                "summary": "Presign an S3 PUT for a direct browser upload",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {
                        "purpose": _str("Asset purpose, e.g. order_form."),
                        "filename": _str("Original filename."),
                        "content_type": _str("MIME type."),
                    },
                    "required": ["purpose", "filename", "content_type"],
                }),
                "responses": {"200": _resp("Presigned URL.", "#/components/schemas/PresignResponse"), "400": _err("Missing field.")},
            },
        },
        "/garage/assets": {
            "post": {
                "tags": ["Assets"], "security": _BEARER,
                "summary": "Register an uploaded asset",
                "description": "Call after the presigned PUT succeeds. Verifies the object + records real size.",
                "requestBody": _inline_body({
                    "type": "object",
                    "properties": {
                        "s3_key": {"type": "string"},
                        "purpose": {"type": "string"},
                        "reference_id": _str("Owning kickoff koid (or other ref)."),
                        "filename": {"type": "string"},
                        "content_type": {"type": "string"},
                        "size_bytes": {"type": "integer", "description": "Fallback; overridden by the real S3 size when readable."},
                    },
                    "required": ["s3_key", "purpose", "reference_id"],
                }),
                "responses": {"201": _resp("Registered.", "#/components/schemas/Asset"), "400": _err("Missing field.")},
            },
        },
    }


@lru_cache(maxsize=1)
def build_spec() -> dict:
    """Assemble (and cache) the OpenAPI 3.0.3 document. Pure/static, so memoized."""
    schemas = {**_request_schemas(), **_response_schemas()}
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Kickstart API",
            "version": "1.0.0",
            "description": "Internal customer-onboarding orchestration API (Garage · Kickstart module). "
            "All `/garage/kickstart/*` routes require a drivetrainer Okta bearer token except the public customer-intake "
            "endpoints. This document is served in non-prod only.",
        },
        "servers": [{"url": "/", "description": "This host"}],
        "tags": [
            {"name": "Kickoffs & drafts"},
            {"name": "Lifecycle & assignment"},
            {"name": "Consultants"},
            {"name": "Customer intake (public)"},
            {"name": "Assets"},
        ],
        "paths": _build_paths(),
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                               "description": "Drivetrainer Okta access token."},
            },
            "schemas": schemas,
        },
    }


def app_undocumented_operations(app) -> list[str]:
    """Safety net: `/garage/kickstart/*` (path, method) pairs registered on the live app but missing from the spec.
    Flask `<koid>`/`<int:asset_id>` → OpenAPI `{koid}`/`{asset_id}`. Empty list = fully documented."""
    import re

    documented = {(p, m.upper()) for p, ops in _build_paths().items() for m in ops}
    # Meta routes (Swagger UI + the spec itself) aren't business operations — exclude from the check.
    meta = {"/garage/kickstart/docs", "/garage/kickstart/openapi.json"}
    missing = []
    for rule in app.url_map.iter_rules():
        path = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", str(rule.rule))
        if not path.startswith(("/garage/kickstart/", "/garage/assets")) or path in meta:
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            if (path, method) not in documented:
                missing.append(f"{method} {path}")
    return sorted(missing)


def swagger_ui_html(spec_url: str) -> str:
    """Minimal Swagger UI page (CDN assets), pointed at `spec_url`. Dev-only, so CDN is fine."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Kickstart API · Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"/>
  <style>body {{ margin: 0; background: #fafafa; }} .topbar {{ display: none; }}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: {spec_url!r},
      dom_id: '#swagger-ui',
      deepLinking: true,
      persistAuthorization: true,
      tryItOutEnabled: true,
    }});
  </script>
</body>
</html>"""
