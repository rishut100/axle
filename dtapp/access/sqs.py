"""Dedicated 'garage-access' SQS queue — same create-by-name/cache + relay-backstop shape as
dtapp/garage/kickstart/sqs.py, copied rather than parameterized-and-shared (decision #4): this module
may become its own service later (same "module now, separable service later" rationale as kickstart),
and a shared queue-helper module would be exactly the kind of coupling that makes that split harder.
The two ~80-line files are cheap to keep in sync by inspection; they are not expected to diverge.
"""
import json
import logging
import threading
from functools import lru_cache

import boto3

from dtapp.garage.core.config import settings
from dtapp.access.constants import ACCESS_QUEUE_NAME, OUTBOX_TYPES
from dtapp.garage.kickstart.repositories import outbox_repo  # shared outbox table (decision #4)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_sqs():
    return boto3.client(
        'sqs',
        region_name=settings.aws_region,
        endpoint_url=settings.garage_sqs_endpoint_url or None,
    )


# Shorter VisibilityTimeout than kickstart's (grant/revoke API calls are single-request-per-tool, not a
# multi-service sequential chain) and a longer retention so a bulk-offboarding day's backlog survives a
# consumer restart.
_QUEUE_ATTRS = {
    "ReceiveMessageWaitTimeSeconds": "20",
    "VisibilityTimeout": "60",
    "MessageRetentionPeriod": "345600",  # 4 days
}
_RELAY_GRACE_SECONDS = 30
_RELAY_BATCH = 25  # higher than kickstart's — a bulk offboarding day can enqueue many rows at once
_queue_url = None
_queue_lock = threading.Lock()


def _get_queue_url():
    global _queue_url
    if _queue_url:
        return _queue_url
    with _queue_lock:
        if _queue_url:
            return _queue_url
        try:
            _queue_url = _get_sqs().create_queue(QueueName=ACCESS_QUEUE_NAME, Attributes=_QUEUE_ATTRS)["QueueUrl"]
        except _get_sqs().exceptions.QueueNameExists:
            _queue_url = _get_sqs().get_queue_url(QueueName=ACCESS_QUEUE_NAME)["QueueUrl"]
        logger.info("[access] SQS queue ready: %s", _queue_url)
        return _queue_url


def _reset_queue_url():
    global _queue_url
    _queue_url = None


def _publish_outbox_row(row):
    _get_sqs().send_message(QueueUrl=_get_queue_url(), MessageBody=json.dumps(row.payload))
    outbox_repo.mark_sent(row.id)
    logger.info("[access] outbox published %s (%s)", row.koid, row.type)


def flush_outbox(aeid=None, grace_seconds=0):
    """Same fast-path/relay-backstop semantics as kickstart's flush_outbox. NEVER raises — safe to call
    right after a web-request commit. `aeid` reuses the Outbox table's `koid` column (a generic row-key
    string, not kickoff-specific — see access_event_repo.create_event_atomic)."""
    try:
        rows = (
            outbox_repo.list_pending_for_koid(aeid) if aeid is not None
            # DB-side type filter (see outbox_repo.list_pending's ENG-90754 docstring note) — this
            # relay sweep only ever touches rows this module itself enqueued.
            else outbox_repo.list_pending(grace_seconds, _RELAY_BATCH, types=OUTBOX_TYPES)
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[access] outbox fetch failed (aeid=%s): %s", aeid, e)
        return
    for row in rows:
        try:
            _publish_outbox_row(row)
        except Exception as e:  # noqa: BLE001
            logger.error("[access] outbox publish failed for %s (%s): %s", row.koid, row.type, e)
