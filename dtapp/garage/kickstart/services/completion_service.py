import logging
import random

from dtapp.garage.kickstart.clients import drive_client
from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import VERIFY_TENANT_FLAG, MOCK_PROVISIONING_FLAG
from dtapp.garage.kickstart.enums import KickoffStatus, YesNo
from dtapp.garage.core.flags import feature_on
from dtapp.garage.kickstart.sqs import enqueue_verify
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart.schemas.drive import DriveTenantResponse
from dtapp.garage.kickstart.schemas.kickoff import DriveAdmin, DriveCreateRequest, DriveTenantConfig, KickoffData, flatten_groups
from dtapp.garage.kickstart.kickoff_steps import post_tenant_created_slack, run_completion_steps

logger = logging.getLogger(__name__)


def _tenant_config(data: dict) -> dict:
    """Build Drive's SignupDataContract.Request from merged kickoff data via DriveCreateRequest.
    firstDayOfWeek is the customer's week_start. Required fields missing → ValidationError
    (fail loud, no silent null to Drive)."""
    kd = KickoffData.from_blob(data)
    request = DriveCreateRequest(
        admin=DriveAdmin(
            email=kd.initial_user_email,
            firstName=kd.initial_user_first_name,
            lastName=kd.initial_user_last_name,
        ),
        subdomain=kd.domain_name,
        requires_eu_hosting=kd.requires_eu_hosting == YesNo.YES,
        config=DriveTenantConfig(
            firstDayOfWeek=kd.week_start,
            currency=kd.currency,
            dateFormat=kd.date_format,
        ),
    )
    return request.model_dump(exclude_none=True)  # drop null lastName (only optional field)


def _create_tenant(koid, data, is_mock=False, existing_tenant_id=None) -> DriveTenantResponse:
    """Synchronous Drive REST create — honours dryrun so local dev without axle_api_key works.

    Dryrun returns a real DriveTenantResponse (typed, like the live path) with tenant_id=0 as the
    recognizable dryrun sentinel — tenant_id is an int now, so a 'dryrun-<koid>' string won't fit.
    is_mock (LD kickstart-mock-provisioning, resolved by the caller) → a fake tenant, no Drive call:
    validates the request like the live path (fail-loud on a malformed kickoff), reuses an
    already-persisted tenant_id on redelivery (idempotent), else a fresh random 2000-3000."""
    if is_mock:
        config = _tenant_config(data)  # same validation as the live path (fail-loud on malformed input)
        tid = existing_tenant_id or random.randint(2000, 3000)
        logger.warning("[kickstart] MOCK create_tenant (LD) for %s — NO real tenant created; "
                       "tenant_id=%s subdomain=%s", koid, tid, config.get("subdomain"))
        return DriveTenantResponse(tenant_id=tid, subdomain=config.get("subdomain"), active=True)
    kd = KickoffData.from_blob(data)
    subdomain = kd.domain_name
    if settings.dryrun_unconfigured and not settings.axle_api_key:
        logger.info("[kickstart] DRYRUN create_tenant for %s — subdomain=%s", koid, subdomain)
        return DriveTenantResponse(tenant_id=0, subdomain=subdomain, active=True)
    return drive_client.create_tenant(koid, _tenant_config(data))


def handle_provisioning(message: dict):
    """Consume an Axle-queue message: {koid}.

    Re-load the kickoff and run provisioning as an effectively-once, resumable sequence
    (create → persist tenant_id → completion → done-marker LAST):
      1. SYNCHRONOUS Drive REST create — idempotent: Drive's InternalAxleApi returns the
         EXISTING tenant on a redelivered subdomain, so a retry never errors or re-creates.
      2. Persist tenant_id immediately, then run completion (Slack rename, academy/connector
         Linears, GSheet, Paaras DM) — every step is checkpointed (ref-present or a
         completed_steps flag), so a redelivery resumes from the incomplete step only.
      3. mark_provisioning_done + AWAITING_CONSULTANT, set AFTER all completion succeeds —
         so a mid-flow failure never orphans the remaining steps.
    Raises on any failure (after recording last_error) so Axle's own SQS queue redelivers
    (bounded; no DLQ — a stuck kickoff surfaces via the Datadog PROVISIONING-stall monitor;
    recovery is the LD-gated re-enqueue, which now resumes cleanly since nothing gates it).
    """
    koid = message.get("koid")
    if not koid:
        logger.warning("[kickstart] provisioning message missing koid: %s", message)
        return

    kickoff = kickoff_repo.get_kickoff(koid)
    if kickoff is None:
        logger.warning("[kickstart] provisioning for unknown koid %s — skipping", koid)
        return

    # Resolve the mock gate once so create + the verify-skip below agree (no mid-run flag-flip race).
    is_mock = feature_on(MOCK_PROVISIONING_FLAG)

    try:
        # 1. create the tenant — idempotent: Drive's InternalAxleApi returns the EXISTING tenant on
        #    a redelivered subdomain, so a retry never errors or double-creates.
        resp = _create_tenant(koid, kickoff.data, is_mock, existing_tenant_id=kickoff.tenant_id)
        tenant_id = resp.tenant_id
        kickoff_repo.set_tenant_id(koid, tenant_id)  # persist immediately (records the created tenant)
        # 2. completion — every step is idempotent / checkpointed (ref-present or a completed_steps
        #    flag), so a redelivery resumes from the incomplete step with no duplicate side-effects.
        data = {**flatten_groups(kickoff.data), "tenant_id": tenant_id}  # flat view for the completion steps
        # Reuse the channel_id from the create so completion's bookmark + canvas steps don't re-SELECT
        # the kickoff row; None (redelivery skipped create) → they fall back to a DB read.
        channel_id = post_tenant_created_slack(koid, data, tenant_id)
        run_completion_steps(koid, data, channel_id=channel_id)
    except Exception as e:  # noqa: BLE001 — record + re-raise so SQS redelivers (bounded; no DLQ)
        kickoff_repo.set_last_error(koid, e)
        logger.exception("[kickstart] provisioning failed for %s", koid)
        raise
    # 3. done-marker LAST — set only after ALL completion succeeded, so a mid-flow failure never
    #    orphans the remaining steps; a redelivery or manual re-enqueue resumes cleanly.
    kickoff_repo.mark_provisioning_done(koid, tenant_id)
    kickoff_repo.set_status_explicit(koid, KickoffStatus.AWAITING_CONSULTANT)
    logger.info("[kickstart] %s completed → AWAITING_CONSULTANT (tenant_id=%s)", koid, tenant_id)

    # post-provisioning safety net — best-effort: a verify-scheduling hiccup must never replay or
    # undo a provisioning that already succeeded. Flag-gated (off locally → skipped entirely).
    if feature_on(VERIFY_TENANT_FLAG) and not is_mock:
        try:
            enqueue_verify(koid, 1)
        except Exception as e:  # noqa: BLE001
            logger.warning("[kickstart] could not schedule tenant verify for %s: %s", koid, e)
