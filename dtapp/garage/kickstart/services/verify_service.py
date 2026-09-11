import logging

from dtapp.garage.kickstart.clients import drive_client, slack_client
from dtapp.garage.core.config import settings
from dtapp.garage.core.constants import (VERIFY_MAX_ATTEMPTS, VERIFY_TENANT_FLAG, MOCK_PROVISIONING_FLAG,
                                         ASK_SRE_SLACK_CHANNEL_ID, SRE_SUBTEAM_ID)
from dtapp.garage.core.flags import feature_on
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart.schemas.kickoff import KickoffData, flatten_groups
from dtapp.garage.kickstart.kickoff_steps import _kickoff_detail_url, step_done
from dtapp.garage.kickstart.sqs import enqueue_verify

logger = logging.getLogger(__name__)

VERIFY_OK = "verify_ok"
VERIFY_ESCALATED = "verify_escalated"


def handle_verify(message: dict):
    """Delayed post-provisioning tenant-health check (kickstart-verify-tenant).

    GETs Drive's GC health endpoint by tenant id (via drive_client): healthy = found + active +
    subdomain & id match. A transport/5xx error propagates so the SAME attempt is retried — GC being
    down is an infra blip, not a verification failure (it must not consume one of the three checks).
    Re-checks at +5/+10/+15 min by self-re-enqueueing; after the 3rd unhealthy check it escalates to
    #ask-sre. Terminal-idempotent (verify_ok / verify_escalated flags) so a redelivery never
    double-escalates.
    """
    koid = message.get("koid")
    attempt = int(message.get("attempt", 1))
    if not koid:
        logger.warning("[kickstart] verify message missing koid: %s", message)
        return
    if not feature_on(VERIFY_TENANT_FLAG):
        logger.info("[kickstart] verify flag off — skipping verify for %s", koid)
        return
    # Defense-in-depth: a mock-provisioned kickoff has a fake tenant_id (no real Drive tenant), so a
    # stray/redelivered verify message must not probe it (would burn attempts + escalate to #ask-sre).
    if feature_on(MOCK_PROVISIONING_FLAG):
        logger.info("[kickstart] mock-provisioning on — skipping verify for %s (no real tenant to probe)", koid)
        return

    kickoff = kickoff_repo.get_kickoff(koid)
    if kickoff is None:
        logger.warning("[kickstart] verify for unknown koid %s — skipping", koid)
        return
    data = flatten_groups(kickoff.data)  # flat read view (step_done reads completed_steps; _escalate reads company_name)
    if step_done(data, VERIFY_OK) or step_done(data, VERIFY_ESCALATED):
        return  # already resolved — idempotent no-op on redelivery

    kd = KickoffData.from_blob(data)
    tenant_id = kd.tenant_id
    if not tenant_id:
        # No id recorded → can't probe; this is a provisioning-completeness gap (the stall monitor's
        # job), not a tenant-health one. Skip rather than poison the queue on int(None).
        logger.warning("[kickstart] verify for %s has no tenant_id — skipping", koid)
        return
    expected_subdomain = kd.domain_name
    health = drive_client.verify_tenant(tenant_id)  # raises on transport/5xx → retry same attempt
    if _is_healthy(health, expected_subdomain, tenant_id):
        kickoff_repo.mark_step_done(koid, VERIFY_OK)
        logger.info("[kickstart] tenant verify OK for %s (id=%s, attempt %s)", koid, tenant_id, attempt)
        return

    if attempt < VERIFY_MAX_ATTEMPTS:
        enqueue_verify(koid, attempt + 1)
        logger.warning("[kickstart] tenant verify unhealthy for %s (attempt %s/%s) — rescheduling",
                       koid, attempt, VERIFY_MAX_ATTEMPTS)
        return

    _escalate(koid, data, tenant_id, expected_subdomain)
    kickoff_repo.mark_step_done(koid, VERIFY_ESCALATED)  # set only after a successful post → no dup escalation


def _is_healthy(health, expected_subdomain, tenant_id) -> bool:
    """Healthy = the tenant exists (found) + active + identity matches what we created
    (subdomain AND id both match — guards against a wrong/missing tenant). health is a
    DriveHealthResponse."""
    if not health.found or not health.active:
        return False
    return (
        health.subdomain == expected_subdomain
        and str(health.tenant_id) == str(tenant_id)
    )


def _escalate(koid, data, tenant_id, subdomain):
    """Post a #ask-sre escalation tagging @sre-team. A post failure raises (→ message redelivers,
    re-escalates) so it keeps trying until it lands; verify_escalated is flagged only after a
    successful post, so escalation fires exactly once."""
    kd = KickoffData.from_blob(data)
    subteam = f"<!subteam^{SRE_SUBTEAM_ID}> " if SRE_SUBTEAM_ID else ""
    kickstart_url = _kickoff_detail_url(koid)
    text = (
        f":rotating_light: *Tenant verification failed* — kickoff *{koid}* ({kd.company_name})\n"
        f"Tenant `id={tenant_id}` / `{subdomain}.drivetrain.ai` did not verify healthy after "
        f"{VERIFY_MAX_ATTEMPTS} checks (+5/+10/+15 min).\n"
        f"{subteam}please investigate.\n"
        f"Kickstart: {kickstart_url}"
    )
    slack_client.post_message(ASK_SRE_SLACK_CHANNEL_ID, text)
    logger.error("[kickstart] tenant verify ESCALATED to #ask-sre for %s (id=%s)", koid, tenant_id)
