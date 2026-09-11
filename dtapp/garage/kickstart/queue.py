import json
import logging
import threading
import time

from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import ASK_SRE_SLACK_CHANNEL_ID, SRE_SUBTEAM_ID
from dtapp.garage.kickstart.clients import slack_client
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart.services.assign_service import handle_assign
from dtapp.garage.kickstart.services.completion_service import handle_provisioning
from dtapp.garage.kickstart.services.kickoff_service import handle_register
from dtapp.garage.kickstart.services.verify_service import handle_verify
from dtapp.garage.kickstart.sqs import (
    _RELAY_GRACE_SECONDS,
    _get_queue_url,
    _get_sqs,
    _reset_queue_url,
    flush_outbox,
)

logger = logging.getLogger(__name__)

_MAX_RECEIVE_COUNT = 3  # give up + dead-letter after this many deliveries (no SQS DLQ)


def _dead_letter(body, error):
    """Drive-style give-up: after _MAX_RECEIVE_COUNT deliveries, stop retrying — record the failure and
    escalate to #ask-sre so a permanently-failing kickoff is actioned (not silently stuck).
    Marks the kickoff FAILED (register/provision/assign only — verify owns no transient status). Best-effort.
    Local/blank channel → escalation skipped (the ERROR log + last_error still record it)."""
    koid = body.get("koid")
    mtype = body.get("type") or "provision"
    logger.error("[kickstart] DEAD-LETTER %s (%s) after %s attempts: %s", koid, mtype, _MAX_RECEIVE_COUNT, error)
    if koid:
        # Only the work types that OWN a transient status get FAILED. A verify dead-letter is a
        # non-blocking health probe on a DONE / CONSULTANT_ASSIGNED kickoff — it must never flip a live,
        # already-provisioned kickoff to FAILED (CONSULTANT_ASSIGNED → FAILED is otherwise a legal edge).
        if mtype in ("register", "provision", "assign"):
            try:
                kickoff_repo.mark_failed(koid)  # primary signal: explicit FAILED
            except Exception as e2:  # noqa: BLE001 — best-effort; must not block the last_error write below
                logger.warning("[kickstart] dead-letter mark_failed failed for %s: %s", koid, e2)
        try:
            kickoff_repo.set_last_error(koid, f"gave up after {_MAX_RECEIVE_COUNT} attempts ({mtype}): {error}")
        except Exception as e2:  # noqa: BLE001 — best-effort
            logger.warning("[kickstart] dead-letter set_last_error failed for %s: %s", koid, e2)
    if settings.is_prod:  # SRE escalation is prod-only (const #ask-sre / @sre-team targets)
        subteam = f"<!subteam^{SRE_SUBTEAM_ID}> " if SRE_SUBTEAM_ID else ""
        try:
            slack_client.post_message(
                ASK_SRE_SLACK_CHANNEL_ID,
                f":rotating_light: *Kickstart {mtype} permanently failed* — kickoff *{koid}* after "
                f"{_MAX_RECEIVE_COUNT} attempts.\n{subteam}please investigate. Last error: {error}")
        except Exception as e2:  # noqa: BLE001 — best-effort; ERROR log + last_error still record it
            logger.warning("[kickstart] dead-letter #ask-sre post failed for %s: %s", koid, e2)


def _consume_message(msg, handlers, default_handler):
    """Process one SQS message: route by type, delete on success. On failure, leave for redelivery
    until ApproximateReceiveCount hits _MAX_RECEIVE_COUNT, then delete + dead-letter (bounded, no DLQ)."""
    body = {}
    try:
        body = json.loads(msg["Body"])
        handlers.get(body.get("type"), default_handler)(body)
        _get_sqs().delete_message(QueueUrl=_get_queue_url(), ReceiptHandle=msg["ReceiptHandle"])
    except Exception as e:  # noqa: BLE001
        recv = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        if recv >= _MAX_RECEIVE_COUNT:
            try:
                _dead_letter(body, e)          # give up: record + escalate (best-effort)
            except Exception as e2:  # noqa: BLE001 — the dead-letter hook must never kill the consumer
                logger.error("[kickstart] dead-letter hook failed for %s: %s", body.get("koid"), e2)
            try:
                _get_sqs().delete_message(QueueUrl=_get_queue_url(), ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e3:  # noqa: BLE001 — a delete failure must not kill the consumer thread
                logger.error("[kickstart] dead-letter delete failed for %s: %s", body.get("koid"), e3)
        else:
            logger.error("[kickstart] consumer error (attempt %s/%s), leaving for redelivery: %s",
                         recv, _MAX_RECEIVE_COUNT, e)


def run_provisioning_consumer():
    """Phase 2/3 consumer (in Axle): for each {koid}, make the synchronous Drive REST
    create call, persist tenant_id, run completion steps, → AWAITING_CONSULTANT.
    Idempotent by koid (Drive's subdomain UNIQUE on create + Axle's provisioning_done_at
    on completion).

    On a successfully handled message, delete it. On error, leave it on the queue
    (bounded SQS redelivery; no DLQ — a never-completing kickoff is marked FAILED at dead-letter,
    which the Datadog stuck-monitor catches, and recovery is the LD-gated re-enqueue)."""
    # type -> handler registry. No "type" / unknown type falls through to handle_provisioning
    # (Phase 2 sync create), as before.
    handlers = {
        "register": handle_register,    # async register-ancillary work; idempotent by koid
        "assign": handle_assign,        # async post-assign work (Monday owners + channel-add); idempotent by koid
        "verify": handle_verify,        # delayed tenant-health check + #ask-sre escalation
    }
    logger.info("[kickstart] kickstart provisioning consumer started")
    while True:
        try:
            flush_outbox(grace_seconds=_RELAY_GRACE_SECONDS)  # relay: publish stragglers (best-effort)
        except Exception as e:  # noqa: BLE001 — a relay error must NOT kill the consumer thread
            logger.error("[kickstart] outbox relay sweep error: %s", e)
        try:
            resp = _get_sqs().receive_message(
                QueueUrl=_get_queue_url(), MaxNumberOfMessages=5, WaitTimeSeconds=20,
                AttributeNames=["ApproximateReceiveCount"])
        except Exception as e:  # noqa: BLE001 — a poll error must NOT kill the consumer thread
            logger.error("[kickstart] provisioning-consumer poll error: %s", e)
            _reset_queue_url()   # self-heal if the local queue vanished (ElasticMQ restart)
            time.sleep(5)     # back off before retrying the poll
            continue
        for msg in resp.get('Messages', []):
            _consume_message(msg, handlers, handle_provisioning)


def start_provisioning_consumer_thread():
    threading.Thread(target=run_provisioning_consumer, daemon=True).start()
