import os

from flask import Blueprint, request

from dtapp.garage.core.auth import require_drivetrainer
from dtapp.garage.core.errors import BadRequestError, register_error_handlers
from dtapp.garage.core import s3_client
from dtapp.garage.assets import repo as asset_repo
from dtapp.garage.assets.policy import get_policy

assets_bp = Blueprint("assets", __name__)
register_error_handlers(assets_bp)


def _reject_bad_extension(name, policy, purpose):
    """content_type is a client-set header (spoofable) — also gate the filename/key extension."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext not in policy.extensions:
        raise BadRequestError(
            f"file extension '{ext or '(none)'}' not allowed for {purpose} "
            f"(allowed: {', '.join(sorted(policy.extensions))})"
        )


@assets_bp.route('/garage/assets/presign', methods=['POST'])
@require_drivetrainer
def presign_asset():
    body = request.get_json(force=True) or {}
    purpose = body.get('purpose')
    filename = body.get('filename')
    content_type = body.get('content_type')
    if not purpose or not filename or not content_type:
        raise BadRequestError('purpose, filename, and content_type are required')
    policy = get_policy(purpose)
    if policy is None:
        raise BadRequestError(f"unsupported asset purpose: {purpose}")
    if content_type not in policy.content_types:
        raise BadRequestError(
            f"content_type '{content_type}' not allowed for {purpose} "
            f"(allowed: {', '.join(sorted(policy.content_types))})"
        )
    _reject_bad_extension(filename, policy, purpose)
    # presign_put binds this Content-Type into the signed URL → the upload is locked to it.
    key = s3_client.asset_key(purpose, filename)
    upload_url = s3_client.presign_put(key, content_type)
    # max_bytes lets the FE reject an oversized file before uploading; the hard cap is enforced
    # at register (a presigned PUT can't carry a content-length condition).
    return {'s3_key': key, 'upload_url': upload_url, 'expires_in': 600, 'max_bytes': policy.max_bytes}, 200


@assets_bp.route('/garage/assets', methods=['POST'])
@require_drivetrainer
def register_asset():
    body = request.get_json(force=True) or {}
    s3_key = body.get('s3_key')
    purpose = body.get('purpose')
    reference_id = body.get('reference_id')
    if not s3_key or not purpose or not reference_id:
        raise BadRequestError('s3_key, purpose, and reference_id are required')
    policy = get_policy(purpose)
    if policy is None:
        raise BadRequestError(f"unsupported asset purpose: {purpose}")
    content_type = body.get('content_type')
    if content_type and content_type not in policy.content_types:
        raise BadRequestError(f"content_type '{content_type}' not allowed for {purpose}")
    _reject_bad_extension(s3_key, policy, purpose)  # gate the authoritative object key's extension
    # Real size from S3 (authoritative); fall back to the client-supplied value on head lag.
    size_bytes = body.get('size_bytes')
    try:
        meta = s3_client.head(s3_key)
        size_bytes = meta.get('ContentLength', size_bytes)
    except Exception:
        pass  # head lag is non-fatal — fall back to client-supplied size
    # Enforce the size cap here (a presigned PUT can't): over-limit → delete the object + reject.
    if size_bytes is not None and size_bytes > policy.max_bytes:
        try:
            s3_client.delete(s3_key)
        except Exception:
            pass
        raise BadRequestError(
            f"file is {size_bytes} bytes; exceeds the {policy.max_bytes}-byte max for {purpose}"
        )
    row = asset_repo.create_asset(
        purpose=purpose,
        reference_id=reference_id,
        s3_key=s3_key,
        filename=body.get('filename'),
        content_type=content_type,
        size_bytes=size_bytes,
    )
    return {
        'id': row.id,
        'purpose': row.purpose,
        'reference_id': row.reference_id,
        's3_key': row.s3_key,
        'filename': row.filename,
        'content_type': row.content_type,
        'size_bytes': row.size_bytes,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }, 201
