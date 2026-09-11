import logging
import os
import re
from functools import lru_cache
from uuid import uuid4

import boto3

from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import GARAGE_S3_BUCKET

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _get_client():
    return boto3.client(
        's3',
        region_name=settings.aws_region,
        endpoint_url=settings.garage_s3_endpoint_url or None,
        aws_access_key_id=settings.garage_s3_access_key or None,
        aws_secret_access_key=settings.garage_s3_secret_key or None,
    )


def asset_key(purpose: str, filename: str) -> str:
    """Build a unique S3 key: crm/<purpose>/<uuid><ext>."""
    ext = os.path.splitext(filename or "")[-1].lower()
    ext = re.sub(r"[^a-z0-9.]", "", ext)  # allowlist: keep only [a-z0-9.]; the crm/<purpose>/<uuid> key already blocks traversal
    return f"crm/{purpose}/{uuid4().hex}{ext}"


def upload(key: str, fileobj, content_type: str) -> None:
    """Upload fileobj to the garage bucket at the given key."""
    _get_client().upload_fileobj(
        fileobj,
        GARAGE_S3_BUCKET,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    logger.info(f"[kickstart] s3 upload: bucket={GARAGE_S3_BUCKET} key={key}")


def presign_put(key: str, content_type: str, expires: int = 600) -> str:
    """Return a presigned PUT URL for direct browser → S3 upload."""
    return _get_client().generate_presigned_url(
        'put_object',
        Params={'Bucket': GARAGE_S3_BUCKET, 'Key': key, 'ContentType': content_type},
        ExpiresIn=expires,
    )


def head(key: str) -> dict:
    """Return head_object metadata (e.g. ContentLength) for the given key."""
    return _get_client().head_object(Bucket=GARAGE_S3_BUCKET, Key=key)


def presign_get(key: str, download_as: str | None = None, expires: int = 604800) -> str:
    """Return a presigned GET URL (default 7 days). Sets Content-Disposition to force a download
    with the original filename when download_as is given."""
    params: dict = {"Bucket": GARAGE_S3_BUCKET, "Key": key}
    if download_as:
        safe_name = download_as.replace('"', "'")  # sanitize filename for header value
        params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
    return _get_client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires,
    )


def get_bytes(key: str) -> bytes:
    """Fetch an object's full bytes (e.g. to re-upload the order form PDF into a Slack channel)."""
    obj = _get_client().get_object(Bucket=GARAGE_S3_BUCKET, Key=key)
    return obj["Body"].read()


def delete(key: str) -> None:
    """Delete an object (soft-delete hook — call explicitly when needed)."""
    _get_client().delete_object(Bucket=GARAGE_S3_BUCKET, Key=key)
    logger.info(f"[kickstart] s3 delete: key={key}")
