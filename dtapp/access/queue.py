"""Access-automation SQS consumer — same bounded-redelivery + dead-letter shape as
dtapp/garage/kickstart/queue.py, wired to access_event_repo/access_steps instead of kickoff_repo/
kickoff_steps. See access/sqs.py's module docstring for why this is a parallel file, not a shared one.
"""
import json
import logging
import threading
import time

from dtapp.garage.core.config import settings
from dtapp.access.enums import AccessEventStatus
from dtapp.access.access_steps.grant import run_grant_steps
from dtapp.access.access_steps.revoke import run_revoke_steps
from dtapp.access.constants import MAX_RECEIVE_COUNT
from dtapp.access.notify import send_log
from dtapp.access.repositories import access_event_repo
from dtapp.access.sqs import _get_sqs, _get_queue_url, _reset_queue_url, flush_outbox, _RELAY_GRACE_SECONDS

# Note: offboarding drift-verification (decision #7/#8) is a CRON DB-scan
# (services/verify_service.run_revoke_verification, registered on the shared CronScheduler in
# main.py), NOT a queued message here — verification needs to survive well past any single SQS
# message's visibility timeout and re-run on a schedule, which a delayed-message chain complicates for
# no benefit over a simple periodic scan.

logger = logging.getLogger(__name__)


def _dead_letter(body, error):
    """After MAX_RECEIVE_COUNT deliveries, stop retrying: record the failure against the event (best
    effort — a dead-lettered *tool* stays in its last checklist status, e.g. 'pending' or 'failed';
    dead-lettering is per-message, not per-event, so it never flips OTHER tools' progress) and escalate
    to the logs channel so it's actioned, not silently stuck."""
    aeid = body.get("aeid")
    mtype = body.get("type") or "grant"
    logger.error("[access] DEAD-LETTER %s (%s) after %s attempts: %s", aeid, mtype, MAX_RECEIVE_COUNT, error)
    if aeid:
        try:
            access_event_repo.record_task_error(aeid, mtype, "dead_letter", str(error))
        except Exception as e2:  # noqa: BLE001 — best-effort
            logger.warning("[access] dead-letter record_task_error failed for %s: %s", aeid, e2)
    send_log(f":rotating_light: *Access {mtype} permanently failed* — event *{aeid}* after "
             f"{MAX_RECEIVE_COUNT} attempts. Please investigate. Last error: {error}")


def _consume_message(msg, handlers, default_handler):
    body = {}
    try:
        body = json.loads(msg["Body"])
        handlers.get(body.get("type"), default_handler)(body)
        _get_sqs().delete_message(QueueUrl=_get_queue_url(), ReceiptHandle=msg["ReceiptHandle"])
    except Exception as e:  # noqa: BLE001
        recv = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        if recv >= MAX_RECEIVE_COUNT:
            try:
                _dead_letter(body, e)
            except Exception as e2:  # noqa: BLE001
                logger.error("[access] dead-letter hook failed for %s: %s", body.get("aeid"), e2)
            try:
                _get_sqs().delete_message(QueueUrl=_get_queue_url(), ReceiptHandle=msg["ReceiptHandle"])
            except Exception as e3:  # noqa: BLE001
                logger.error("[access] dead-letter delete failed for %s: %s", body.get("aeid"), e3)
        else:
            logger.error("[access] consumer error (attempt %s/%s), leaving for redelivery: %s",
                         recv, MAX_RECEIVE_COUNT, e)


def _handle_grant(body):
    aeid = body["aeid"]
    access_event_repo.set_status(aeid, AccessEventStatus.IN_PROGRESS)
    event = access_event_repo.get_event(aeid)
    run_grant_steps(aeid, event.checklist)


def _handle_revoke(body):
    aeid = body["aeid"]
    access_event_repo.set_status(aeid, AccessEventStatus.IN_PROGRESS)
    event = access_event_repo.get_event(aeid)
    run_revoke_steps(aeid, event.checklist)


def run_access_consumer():
    handlers = {"grant": _handle_grant, "revoke": _handle_revoke}
    logger.info("[access] access-automation consumer started")
    while True:
        try:
            flush_outbox(grace_seconds=_RELAY_GRACE_SECONDS)
        except Exception as e:  # noqa: BLE001
            logger.error("[access] outbox relay sweep error: %s", e)
        try:
            resp = _get_sqs().receive_message(
                QueueUrl=_get_queue_url(), MaxNumberOfMessages=10, WaitTimeSeconds=20,
                AttributeNames=["ApproximateReceiveCount"])
        except Exception as e:  # noqa: BLE001
            logger.error("[access] consumer poll error: %s", e)
            _reset_queue_url()
            time.sleep(5)
            continue
        for msg in resp.get('Messages', []):
            _consume_message(msg, handlers, _handle_grant)


def start_access_consumer_thread():
    threading.Thread(target=run_access_consumer, daemon=True).start()
