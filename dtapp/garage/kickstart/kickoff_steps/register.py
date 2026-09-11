"""Phase 1 register steps (cleanup → Linear → GTM). The internal Slack channel is NOT created here —
it's created once after the tenant exists (see completion.run_completion_steps) so it can be named
int-<tenantId>-<subdomain>. The intake email is sent by the service AFTER these, as the final
all-or-nothing commit gate. Each raises on failure so the async register handler records last_error
and SQS redelivers."""

import logging

from dtapp.garage.kickstart.clients import slack_client, linear_client, drive_client, stringer_client
from dtapp.garage.core import s3_client, constants
from dtapp.garage.core.config import settings
from dtapp.garage.kickstart.recipients import recipients
from dtapp.garage.kickstart import notify
from dtapp.garage.assets import repo as asset_repo
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.kickstart.schemas.kickoff import KickoffData, order_form_filename
from dtapp.garage.kickstart.enums import YesNo
from dtapp.garage.kickstart.prompts import TENANT_CONTEXT_PROMPT

from dtapp.garage.kickstart.kickoff_steps._runner import dryrun as _dryrun, best_effort
from dtapp.garage.kickstart.kickoff_steps.common import _kickoff_detail_url

logger = logging.getLogger(__name__)


def _cleanup_demo_users(koid, data):
    kd = KickoffData.from_blob(data)
    tid = kd.poc_tenant_id
    if not tid:
        logger.info("[kickstart] cleanup_demo_users skipped for %s: no poc_tenant_id", koid)
        return "skipped: no poc_tenant_id"
    if settings.dryrun_unconfigured and not settings.axle_api_key:
        return _dryrun("cleanup_demo_users", {"tenant": tid, "action": "cleanup non-drivetrain users"})
    logger.info("[kickstart] cleanup_demo_users start koid=%s tenant=%s", koid, tid)
    return drive_client.cleanup_demo_users(tid)


def _sim_linear_url(koid, suffix="", prefix="KIC") -> str:
    """Single source for dry-run (no-key) simulated Linear issue URLs."""
    return f"https://linear.app/drivetrain/issue/{prefix}-SIM-{koid}" + (f"-{suffix}" if suffix else "")


def _kickoff_desc(koid, data, tenant_id=None, channel_name=None) -> str:
    """KO org-issue description. tenant_id + internal channel are TBD at register; update_linear_tid
    injects both later (once Drive creates the tenant and the internal channel is created)."""
    kd = KickoffData.from_blob(data)
    detail_url = _kickoff_detail_url(koid)
    return "\n".join([
        f"Automated kickoff tracker — ko-id **{koid}**.", "",
        f"* Kickstart: {detail_url}",
        f"* Tenant: {kd.company_name}",
        f"* Kickoff date: {kd.kickoff_date or 'TBD'}",
        f"* Contract start: {kd.contract_start_date or 'TBD'}",
        f"* Time zone: {kd.time_zone or 'TBD'}",
        f"* Channel of communication: {kd.channel_of_communication or 'TBD'}",
        f"* Internal Slack channel: {channel_name or 'TBD'}",
        f"* Tenant ID: {tenant_id or 'TBD'}",
    ])


# Sentinel for the AE assignee — the intro-email issue goes to registered_by, resolved at runtime.
_AE = object()

# Exact title of the "create external channel" sub-issue — skipped when the AE already supplied one.
_EXTERNAL_CHANNEL_SUBISSUE_TITLE = "Set up external Slack channel"

# Exact title of the "Update Kickoff Deck" sub-issue — its id is stashed in references at creation so
# the /assign flow can reassign it to the consultant + ping them (see assign.py _deck_linear).
_DECK_SUBISSUE_TITLE = "Update Kickoff Deck"

# Kickoff sub-issues seeded under the KO parent at register: (title, body, assignee). assignee is a
# recipients Linear id, or _AE (registered_by, resolved at runtime). All open Urgent + Todo. The
# Academy access request needs the intake academy_users, so it's created later at completion, not here.
_KICKOFF_SUBISSUES = [
    ("Send intro email and introduce the customer team",
     "Send the introduction email to the customer and introduce the Drivetrain project team "
     "(consultant + onboarding). This starts the thread the rest of the tasks build on.",
     _AE),
    (_EXTERNAL_CHANNEL_SUBISSUE_TITLE,
     "Customer-facing channel. Only create it if none was already entered in the sales registry. "
     "Confirm whether the customer uses Slack or Teams first.",
     recipients.linear_jason),
    ("Add support bot to the external channel",
     "Add the Drivetrain support bot to the external customer channel so Heimdall can operate there.",
     recipients.linear_fauzan),
    ("Add Slack ID / Teams Conversation ID to the Master Tenant Sheet",
     "Add the external channel's Slack ID / Teams Conversation ID to the Master Tenant Sheet so "
     "Heimdall works for this tenant.",
     recipients.linear_fauzan),
    ("Confirm Academy access has been granted",
     "Confirm the customer's Academy access request (raised separately) is closed and access is granted.",
     recipients.linear_jason),
    ("Verify the external channel is working on Heimdall",
     "Verify the external channel is wired up and working on Heimdall. Add the channel name below if "
     "the AE has shared it; otherwise tag Fauzan to add it.",
     recipients.linear_ankit),
    ("Confirm the kickoff is booked on the calendar",
     "Confirm the kickoff session is booked on the calendar with the customer.",
     recipients.linear_jason),
    ("Send the pre-kickoff checklist",
     "Send the pre-kickoff checklist to the customer (in the intro thread). Questions:\n"
     "1. Who is responsible for the success of Drivetrain on your side?\n"
     "2. What would a successful implementation look like to you?\n"
     "3. Would you be open to starting the data integrations before kickoff? - only if Kickoff is a week out\n"
     "4. Collate the list of reports and models you want to see on Drivetrain.",
     recipients.linear_jason),
    ("Collate pre-kickoff checklist answers and post them in the Slack channel",
     "Collect the customer's answers to the pre-kickoff checklist and post them in the Slack channel "
     "for the project team.",
     recipients.linear_jason),
    (_DECK_SUBISSUE_TITLE,
     "Create a copy of the kickoff deck and update it for this customer.\n\n"
     "Then bookmark on the int slack channel\n\n"
     "Template: https://docs.google.com/presentation/d/1StzpWQPHzitKw67H8DFGcncDkHlGlanY2pS9DHx0PmQ/edit",
     recipients.linear_jason),
    ("Make sure the consultant is added to the external channel",
     "Once the consultant is assigned, make sure they're added to the external customer channel.",
     recipients.linear_jason),
    ("Make sure the consultant is added to the kickoff invite",
     "Once the consultant is assigned, make sure they're on the kickoff calendar invite.",
     recipients.linear_jason),
]


def _create_linear_tree(koid, data) -> dict:
    """At register: create the kickoff Linear tree — parent 'Kickoff: [Tenant]' + the kickoff
    sub-issues (_KICKOFF_SUBISSUES). All open Urgent + Todo, all TID-independent + register-data-only.
    The Academy access request + per-connector issues are added later, after Drive creates the tenant
    (see run_completion_steps); update_linear_tid injects the TID + internal channel into the parent
    then. Best-effort — Linear never breaks the all-or-nothing register. Returns refs for
    data['references'].

    Idempotent under SQS redelivery: if the tree was already created (kickoff_linear_id ref present),
    return the existing linear refs instead of creating a duplicate tree."""
    kd = KickoffData.from_blob(data)
    existing = data.get("references") or {}
    if existing.get("kickoff_linear_id"):
        return {k: existing[k] for k in ("kickoff_linear_url", "kickoff_linear_id")
                if existing.get(k) is not None}
    title = f"Kickoff: {kd.company_name}"
    if settings.dryrun_unconfigured and not settings.linear_api_key:
        _dryrun("create_linear_template", {"title": title, "subs": len(_KICKOFF_SUBISSUES) + 1})
        return {"kickoff_linear_url": _sim_linear_url(koid), "kickoff_linear_id": f"sim-{koid}"}

    def _mk(t, desc=None, assignee=None, parent=None):
        try:
            return linear_client.create_issue(
                recipients.linear_team, t, description=desc, assignee_id=assignee, parent_id=parent,
                priority=constants.LINEAR_PRIORITY_URGENT, state_id=recipients.kickoff_state_id)
        except Exception as e:  # noqa: BLE001 — best-effort per issue
            logger.warning("[kickstart] linear '%s' failed for %s: %s", t, koid, e)
            return None

    refs = {}
    parent = _mk(title, desc=_kickoff_desc(koid, data), assignee=recipients.linear_jason)
    if not parent:
        return refs  # parent create failed → skip sub-issues (don't create orphan top-level issues)
    pid = parent.id
    refs["kickoff_linear_url"], refs["kickoff_linear_id"] = parent.url, parent.id
    ae_id = linear_client.resolve_user_id(kd.registered_by)  # dynamic AE (registered_by) → best-effort
    # Skip the "create external channel" sub-issue when the AE already has one — no setup task needed.
    subissues = _KICKOFF_SUBISSUES
    if kd.external_channel_created == YesNo.YES:
        subissues = [s for s in subissues if s[0] != _EXTERNAL_CHANNEL_SUBISSUE_TITLE]
    for sub_title, body, assignee in subissues:
        if assignee is _AE:
            assignee = ae_id
            # AE not resolvable → leave unassigned but name the AE inline so it's still actionable.
            if not assignee and kd.registered_by:
                body = f"{body}\n\nAE: {kd.registered_by}"
        issue = _mk(sub_title, desc=body, assignee=assignee, parent=pid)
        # Stash the deck sub-issue id/url so /assign can reassign it to the consultant + ping them.
        if issue and sub_title == _DECK_SUBISSUE_TITLE:
            refs["deck_linear_id"], refs["deck_linear_url"] = issue.id, issue.url
    return refs


def _handoff_website(data):
    """Tenant website — a top-level Tenant field."""
    kd = KickoffData.from_blob(data)
    return (kd.company_website_url or "").strip() or None


def _gtm_announce(koid, data):
    """Post the GTM 'new signing' summary to the commercial-kickoff channel (prod) / #shahbaz-dev-test
    (non-prod) — mirrors the manual post: signing + customer site + domain/timezone/start-date/intro-email
    status, @-mentioning the GTM notify list, with the order-form PDF attached. Returns a permalink/marker
    for the gtm_announce ref. Best-effort (caller guards). Honors the dryrun guard via slack_client."""
    kd = KickoffData.from_blob(data)
    channel = recipients.announce_channel
    if not channel:
        return None
    # Resolve the submitter (registered_by) → Slack id for a "Registered by" credit line (so the team
    # can congratulate the AE who closed it). Best-effort: unresolvable → None → fall back to the email.
    reg_email = kd.registered_by
    submitter_id = slack_client.resolve_user_id(reg_email)
    # cc the GTM notify team (minus the submitter to avoid a double @-mention).
    cc = " ".join(notify.mention(uid) for uid in recipients.gtm_notify_slack_ids if uid and uid != submitter_id)
    lines = [
        ":tada: *New Signing*",
        f"Customer Name: {kd.company_name or '—'}",
        f"Customer Website: {_handoff_website(data) or '—'}",
        f"Timezone: {kd.time_zone or '—'}",
        f"Kickoff date: {kd.kickoff_date or 'TBD'}",
        f"Contract start: {kd.contract_start_date or 'TBD'}",
    ]
    if cc:
        lines.append(f"cc {cc}")
    credit = notify.mention(submitter_id) if submitter_id else (reg_email or None)
    if credit:
        lines.append(f"Registered by {credit}")
    text = "\n".join(lines)
    # Attach the order-form PDF if one's on file (mirrors the manual "+ order form attached"); force the
    # canonical name over the AE's arbitrary upload name.
    rows = asset_repo.list_assets(koid, "order_form")
    if rows:
        name = order_form_filename(kd.company_name, rows[-1].filename)
        with best_effort("gtm order-form attach", koid):
            content = s3_client.get_bytes(rows[-1].s3_key)
            return slack_client.upload_file(channel, name, content, title=name, initial_comment=text)
    return slack_client.post_message(channel, text)


def _fetch_tenant_context(koid, data):
    """Fetch a sales→implementation handoff brief from f_stringer's pre-sales agent (the customer's
    sales calls) so the FE Context tab + the internal-channel canvas can show it. Returns the brief
    markdown, or None when there's no company / dry-run. Best-effort (caller guards)."""
    kd = KickoffData.from_blob(data)
    company = (kd.company_name or "").strip()
    if not company:
        return None
    if settings.dryrun_unconfigured and not settings.stringer_api_key:
        _dryrun("fetch_tenant_context", {"company": company})
        return None
    # .replace (not .format): a company name containing { or } would raise KeyError inside .format.
    return stringer_client.fetch_pre_sales_context(company, TENANT_CONTEXT_PROMPT.replace("{company}", company))


def run_register_steps(koid, data) -> dict:
    """Run the retry-safe ancillary register steps in order: cleanup → Linear → GTM. NO Slack channel
    here — it's created post-tenant (completion) so it can carry the real tenantId + subdomain.
    Raises on the first failure so the async register handler records last_error and SQS redelivers.
    Returns refs to merge into kickoff.data (kickoff_linear_*, gtm_announce).

    Idempotent under SQS redelivery: cleanup (soft-delete) is idempotent; Linear is ref-guarded (skip
    the create + return the existing ref) so a retry never builds a duplicate tree; the GTM post is
    ref-guarded too. The Monday workspace + internal Slack channel are created later, at provisioning,
    once the tenant id exists."""
    # Persist refs after each step (not at the end) so a timeout-retry can't duplicate the Linear tree / GTM post.
    existing = data.get("references") or {}
    _cleanup_demo_users(koid, data)
    refs = dict(_create_linear_tree(koid, data))     # Linear template shell (ref-guarded; no dup tree)
    kickoff_repo.merge_references(koid, refs)
    # GTM announce — post "new signing" to the commercial-kickoff channel (prod) / #shahbaz-dev-test
    # (non-prod), both env-resolved via recipients.announce_channel. Ref-guarded (no dup post on
    # redelivery) + best-effort (a gtm post must never fail the all-or-nothing register).
    if existing.get("gtm_announce"):
        refs["gtm_announce"] = existing["gtm_announce"]
    else:
        with best_effort("gtm announce", koid):
            gtm = _gtm_announce(koid, data)
            refs["gtm_announce"] = gtm
            kickoff_repo.merge_references(koid, {"gtm_announce": gtm})
    # Pre-sales context from f_stringer; ref-guarded (skip on redelivery), best-effort (never fails register).
    if existing.get("tenant_context"):
        refs["tenant_context"] = existing["tenant_context"]
    else:
        try:
            ctx = _fetch_tenant_context(koid, data)
            if ctx:
                refs["tenant_context"] = ctx
                kickoff_repo.merge_references(koid, {"tenant_context": ctx})
                logger.info("[kickstart] sales context stored for %s (%d chars)", koid, len(ctx))
            else:
                logger.info("[kickstart] sales context empty/skipped for %s", koid)
            kickoff_repo.clear_task_error(koid, "sales_registry", "sales_context")
        except Exception as e:  # noqa: BLE001 — a Stringer/LLM failure never fails the register
            logger.warning("[kickstart] sales context fetch failed for %s: %s", koid, e)
            kickoff_repo.record_task_error(koid, "sales_registry", "sales_context", str(e))
    return refs
