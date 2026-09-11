import json
import logging
from datetime import datetime, timezone

from flask import request
from pydantic import ValidationError

logger = logging.getLogger(__name__)

# Routes whose body must NOT be enveloped: Swagger UI needs the raw OpenAPI doc; the docs page is HTML.
_ENVELOPE_SKIP = {"/garage/kickstart/openapi.json", "/garage/kickstart/docs"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_CODE_BY_STATUS = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    409: "conflict", 422: "validation_failed", 500: "internal_error", 502: "service_error",
}


def _error_envelope(status_code: int, message, code=None):
    """Uniform error envelope (Forge-style: consistent success flag + snake_case status_code).
    `code` = stable machine-readable code for FE branching — ALWAYS present (status-derived fallback)."""
    return {
        "success": False,
        "status_code": status_code,
        "code": code or _CODE_BY_STATUS.get(status_code, "error"),
        "timestamp": _now_iso(),
        "path": request.path,
        "message": message,
    }


class GarageError(Exception):
    """Base for all Garage API errors."""

    status_code = 500
    code = "garage_error"

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


class ConfigError(GarageError):
    """A required setting/credential is missing or invalid."""

    status_code = 500
    code = "config_error"


class ServiceError(GarageError):
    """An upstream/external service call failed. Carries the service name."""

    status_code = 502
    code = "service_error"

    def __init__(self, service: str, message: str = ""):
        self.service = service
        detail = f"{service}: {message}" if message else f"{service} call failed"
        super().__init__(detail)


class ForbiddenError(GarageError):
    status_code = 403
    code = "forbidden"


class NotFoundError(GarageError):
    status_code = 404
    code = "not_found"


class ConflictError(GarageError):
    status_code = 409
    code = "conflict"


class BadRequestError(GarageError):
    """A malformed/incomplete request (missing required field, etc.)."""

    status_code = 400
    code = "bad_request"


def register_error_handlers(bp):
    """Register the error handlers (error envelope) AND the after_request success-envelope wrapper, so
    every /garage/* JSON response carries the uniform {success, ...} shape."""

    @bp.errorhandler(GarageError)
    def handle_garage_error(e):
        # Covers ConfigError/ServiceError/Forbidden/NotFound/Conflict/BadRequest (all subclasses).
        return _error_envelope(e.status_code, str(e), e.code), e.status_code

    @bp.errorhandler(ValidationError)
    def handle_validation_error(e):
        # include_url/include_context dropped: a custom field_validator's raise ValueError puts the raw
        # (non-JSON-serializable) exception in ctx → json.dumps would 500. loc/msg/type/input suffice.
        return _error_envelope(400, e.errors(include_url=False, include_context=False), "validation_failed"), 400

    @bp.errorhandler(Exception)
    def handle_unexpected(e):
        # Catch-all so an unhandled crash still returns the error envelope (not Flask's HTML 500).
        logger.exception("[kickstart] unhandled error")
        return _error_envelope(500, "Internal server error", "internal_error"), 500

    # Envelope EVERY /garage JSON response. Handlers above already emit the error envelope (skipped via
    # the `success`-present check); this also catches 2xx payloads AND bare error returns that bypass
    # the handlers (e.g. require_drivetrainer's {'error':'unauthorized'} 401, ad-hoc 400s).
    @bp.after_request
    def _wrap(response):
        if request.path in _ENVELOPE_SKIP or not response.is_json:
            return response
        body = response.get_json(silent=True)
        if isinstance(body, dict) and "success" in body:
            return response  # already enveloped (error handlers above)
        if response.status_code < 400:
            response.set_data(json.dumps({
                "success": True,
                "path": request.path,
                "status_code": response.status_code,
                "message": "OK",
                "data": body,
            }))
        else:
            # Bare error return (no envelope) → reshape. Prefer an explicit detail/error/message field.
            if isinstance(body, dict):
                msg = body.get("detail") or body.get("error") or body.get("message")
                code = body.get("error")
            else:
                msg, code = body, None
            response.set_data(json.dumps(_error_envelope(response.status_code, msg or "error", code)))
        return response
