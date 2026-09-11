import json
import logging
import threading
from functools import lru_cache

import boto3

from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import VERIFY_DELAY_SECONDS, KICKSTART_PROVISIONING_QUEUE_NAME
from dtapp.garage.kickstart.repositories import outbox_repo

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _get_sqs():
    return boto3.client(
        'sqs',
        region_name=settings.aws_region,
        endpoint_url=settings.garage_sqs_endpoint_url or None,
    )

# Queue tuning (create-time attributes). VisibilityTimeout must exceed the slowest handler
# (provisioning does a sync Drive create + Slack/Monday/Linear); retention is the bounded-redelivery
# + manual-re-enqueue recovery window. No RedrivePolicy — the consumer's receive-count cap is the DLQ.
_QUEUE_ATTRS = {
    "ReceiveMessageWaitTimeSeconds": "20",
    "VisibilityTimeout": "300",
    "MessageRetentionPeriod": "345600",  # 4 days
}
_RELAY_GRACE_SECONDS = 30  # fast-path grace before the relay adopts a straggler outbox row
_RELAY_BATCH = 10          # max rows per relay sweep
_queue_url = None
_queue_lock = threading.Lock()


def _get_queue_url():
    """Lazily create-by-name (idempotent) and cache the queue URL. Works for both local ElasticMQ
    (endpoint set) and real SQS. create_queue returns the URL; a QueueNameExists (same name, different
    attrs) falls back to get_queue_url so an existing queue is reused as-is."""
    global _queue_url
    if _queue_url:
        return _queue_url
    with _queue_lock:
        if _queue_url:
            return _queue_url
        name = KICKSTART_PROVISIONING_QUEUE_NAME
        try:
            _queue_url = _get_sqs().create_queue(QueueName=name, Attributes=_QUEUE_ATTRS)["QueueUrl"]
        except _get_sqs().exceptions.QueueNameExists:
            _queue_url = _get_sqs().get_queue_url(QueueName=name)["QueueUrl"]
        logger.info("[kickstart] SQS queue ready: %s", _queue_url)
        return _queue_url


def _reset_queue_url():
    """Drop the cache so the next _get_queue_url re-creates — self-heals a local ElasticMQ restart."""
    global _queue_url
    _queue_url = None


def enqueue_verify(koid, attempt: int):
    """Schedule a delayed tenant-health verify (kickstart-verify-tenant) on the same queue.
    Typed message so the consumer routes to handle_verify; DelaySeconds spaces the checks."""
    _get_sqs().send_message(
        QueueUrl=_get_queue_url(),
        MessageBody=json.dumps({"koid": koid, "type": "verify", "attempt": attempt}),
        DelaySeconds=VERIFY_DELAY_SECONDS,
    )
    logger.info("[kickstart] enqueued tenant verify for %s (attempt %s)", koid, attempt)


def _publish_outbox_row(row):
    """Publish one outbox row's payload to SQS (the exact MessageBody) then mark it sent, so the relay
    never re-publishes it. The consumer is idempotent-by-koid, so a rare double-publish is harmless."""
    _get_sqs().send_message(QueueUrl=_get_queue_url(), MessageBody=json.dumps(row.payload))
    outbox_repo.mark_sent(row.id)
    logger.info("[kickstart] outbox published %s (%s)", row.koid, row.type)


def flush_outbox(koid=None, grace_seconds=0):
    """Publish pending outbox rows to SQS (best-effort). With a koid → the just-committed rows for that
    koid (fast path, right after the atomic commit). Without → the relay backstop: rows still pending
    after grace_seconds (a fast-path miss or a crash between commit and publish). Both the DB fetch and
    each per-row publish are guarded — a failure is logged and leaves the row pending for the next relay
    sweep. It NEVER raises (safe to call from a web-request thread right after the commit)."""
    try:
        rows = (
            outbox_repo.list_pending_for_koid(koid) if koid is not None
            # Scoped to kickstart's own types since ENG-90754 (dtapp/access) added new type values on
            # this same shared table — without this, a stuck access-module row could be relayed onto
            # THIS queue by mistake.
            else outbox_repo.list_pending(grace_seconds, _RELAY_BATCH, types=["register", "provision", "assign"])
        )
    except Exception as e:  # noqa: BLE001 — best-effort; a fetch failure leaves rows for the next sweep
        logger.error("[kickstart] outbox fetch failed (koid=%s): %s", koid, e)
        return
    for row in rows:
        try:
            _publish_outbox_row(row)
        except Exception as e:  # noqa: BLE001 — best-effort; leave pending for the relay to retry
            logger.error("[kickstart] outbox publish failed for %s (%s): %s", row.koid, row.type, e)
