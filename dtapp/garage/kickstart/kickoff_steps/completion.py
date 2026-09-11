"""Phase 3 completion (post-tenant): CREATE the internal Slack channel (int-<tenantId>-<subdomain>)
with its order-form / timezone-canvas / bot+AE setup and a "Tenant Created" post, then Monday workspace
→ academy + connector Linears → inject the tenant id into the parent → Paaras DM. The Linear template
shell was created at register; everything here is added after Drive creates the tenant — the channel
included, so its name can carry the real tenant id + chosen subdomain."""

import logging

from dtapp.garage.kickstart.clients import slack_client, linear_client, monday_client
from dtapp.garage.core import constants, s3_client
from dtapp.garage.core.config import settings
from dtapp.garage.kickstart.recipients import recipients
from dtapp.garage.kickstart.repositories import kickoff_repo
from dtapp.garage.assets import repo as asset_repo
from dtapp.garage.kickstart.schemas.kickoff import KickoffData, order_form_filename

from dtapp.garage.kickstart.kickoff_steps._runner import dryrun as _dryrun, run_step, step_done, best_effort
from dtapp.garage.kickstart.kickoff_steps.common import (
    _add_channel_bookmark, _channel_id_or_load, _kickoff_detail_url,
)
from dtapp.garage.kickstart.kickoff_steps.register import _kickoff_desc, _sim_linear_url

logger = logging.getLogger(__name__)


def _internal_channel_name(tenant_id, data):
    """The internal Slack channel name, created ONCE post-tenant: int-<tenantId>-<subdomainSlug>.
    Subdomain = the customer's chosen intake domain_name (falls back to the tenant/company name)."""
    kd = KickoffData.from_blob(data)
    subdomain = kd.domain_name or kd.company_name or "kickoff"
    return slack_client.slugify(f"int-{tenant_id}-{subdomain}")


def _upload_order_form(koid, channel_id, company_name=None):
    """Best-effort: fetch the kickoff's order-form PDF from S3 and upload it into the channel (so it
    lives in the channel like the manual kickoffs). Force-renamed to a canonical, identifiable name (the
    AE uploads with arbitrary names). No order form on file → no-op."""
    rows = asset_repo.list_assets(koid, "order_form")
    if not rows:
        return "skipped: no order form"
    row = rows[-1]  # latest upload for this kickoff
    name = order_form_filename(company_name, row.filename)
    try:
        content = s3_client.get_bytes(row.s3_key)
        return slack_client.upload_file(channel_id, name, content, title=name)
    except Exception as e:  # noqa: BLE001 — best-effort; never fail completion over a Slack/S3 hiccup
        logger.warning("[kickstart] order form upload failed for %s: %s", koid, e)
        return f"failed: {e}"


def _render_timezone_canvas(kd):
    """Canvas markdown: the tenant's time zone (company name as subtitle). The canvas TITLE is set
    separately via create_channel_canvas — Slack does not derive it from a body H1, so no `# ` here."""
    tz = (kd.time_zone or "").strip()
    if not tz:
        return None
    lines = []
    if kd.company_name:
        lines += [f"_{kd.company_name}_", ""]
    lines.append(f"- **Time Zone:** {tz}")
    return "\n".join(lines)


def _finalize_timezone_canvas(koid, data, channel_id=None) -> None:
    """Render the tenant's time zone as the channel's canvas — DEFERRED to the very END of
    completion (after the Monday/Linear work) on purpose: a freshly-created channel's canvas backend
    isn't ready for the first few seconds after conversations.create, so creating it mid channel-setup
    fails with `canvas_tab_creation_failed`. Running it last gives Slack ample elapsed time, so it
    succeeds on the first try. Reuses the run's `channel_id` when threaded, else re-loads it from the
    kickoff row (idempotent-resume); idempotent (timezone_canvas flag → redelivery/re-drive skip) +
    best-effort (a failure is logged + recorded, never crashes completion)."""
    if step_done(data, "timezone_canvas"):
        return
    kd = KickoffData.from_blob(data)
    md = _render_timezone_canvas(kd)
    if not md:
        return
    channel_id = _channel_id_or_load(koid, channel_id)
    if not channel_id:
        return
    try:
        slack_client.create_channel_canvas(channel_id, md, title=f"Time Zone: {kd.time_zone}")
        kickoff_repo.mark_step_done(koid, "timezone_canvas")  # once-only; a redelivery skips
    except Exception as e:  # noqa: BLE001 — best-effort; left un-done so a re-drive retries
        logger.warning("[kickstart] timezone canvas failed for %s: %s", koid, e)
        kickoff_repo.record_task_error(koid, "provisioning", "timezone_canvas", str(e))


def _render_hubspot_canvas(kd):
    deal = str(kd.hubspot_deal_id or "").strip()
    if not deal:
        return None
    lines = []
    if kd.company_name:
        lines += [f"_{kd.company_name}_", ""]
    lines.append(f"- **HubSpot Deal ID:** {deal}")
    return "\n".join(lines)


def _finalize_hubspot_canvas(koid, data, channel_id=None) -> None:
    """Second channel canvas tab (the HubSpot deal id) — deferred + best-effort like the time-zone
    canvas, idempotent via the hubspot_canvas step flag."""
    if step_done(data, "hubspot_canvas"):
        return
    kd = KickoffData.from_blob(data)
    md = _render_hubspot_canvas(kd)
    if not md:
        return
    channel_id = _channel_id_or_load(koid, channel_id)
    if not channel_id:
        return
    try:
        slack_client.create_channel_canvas(channel_id, md, title="Hubspot Deal ID")
        kickoff_repo.mark_step_done(koid, "hubspot_canvas")
    except Exception as e:  # noqa: BLE001 — best-effort; left un-done so a re-drive retries
        logger.warning("[kickstart] hubspot canvas failed for %s: %s", koid, e)
        kickoff_repo.record_task_error(koid, "provisioning", "hubspot_canvas", str(e))


# Human titles for the handoff-checklist sections, in render order. Only sections with content
# (at least one non-empty field) are rendered.
_HANDOFF_SECTIONS = [
    ("key_customer_details", "Key Customer Details"),
    ("stakeholder_map", "Stakeholder Map"),
    ("poc_details", "POC Details"),
    ("contract_commitments", "Contract Commitments"),
    ("risk_flags", "Risk Flags"),
    ("sales_notes", "Sales Notes"),
]

# poc_* detail sub-fields — rendered only when a POC was actually done (poc_done == "Yes").
_POC_DETAIL_FIELDS = ("poc_dt_team", "poc_customer_team", "poc_outcomes", "poc_model_files")


def _humanize(key: str) -> str:
    """snake_case field/section key → Title Case label (poc_dt_team → Poc Dt Team)."""
    return key.replace("_", " ").title()


def _render_handoff_canvas(kd):
    """Canvas markdown for the Sales → CS handoff checklist (kd.handoff_checklist, a grouped dict).
    Renders each non-empty section as a `## <Section>` header with `- **<Field>:** <value>` lines;
    empty/None values are skipped. For poc_details the poc_* detail sub-fields are dropped when no
    POC was done. Returns None when the checklist is empty/absent."""
    checklist = kd.handoff_checklist
    if not isinstance(checklist, dict) or not checklist:
        return None
    blocks = []
    for section_key, section_title in _HANDOFF_SECTIONS:
        section = checklist.get(section_key)
        if not isinstance(section, dict):
            continue
        # poc_details: hide the conditional poc_* sub-fields unless a POC was actually done.
        skip_fields = set()
        if section_key == "poc_details" and str(section.get("poc_done") or "").strip() != "Yes":
            skip_fields = set(_POC_DETAIL_FIELDS)
        rows = []
        for field, value in section.items():
            if field in skip_fields:
                continue
            # list-valued fields (e.g. model-file links) join to a readable line; scalars stringify.
            if isinstance(value, (list, tuple)):
                items = [str(x).strip() for x in value if str(x).strip()]
                text = ", ".join(items)
            else:
                text = str(value).strip() if value is not None else ""
            if not text:
                continue
            rows.append(f"- **{_humanize(field)}:** {text}")
        if rows:
            blocks.append(f"## {section_title}\n" + "\n".join(rows))
    if not blocks:
        return None
    return "\n\n".join(blocks)


def _finalize_handoff_canvas(koid, data, channel_id=None) -> None:
    """Render the Sales Handoff checklist as a channel canvas — deferred + best-effort like the
    time-zone canvas, idempotent via the handoff_canvas step flag."""
    if step_done(data, "handoff_canvas"):
        return
    kd = KickoffData.from_blob(data)
    md = _render_handoff_canvas(kd)
    if not md:
        return
    channel_id = _channel_id_or_load(koid, channel_id)
    if not channel_id:
        return
    try:
        slack_client.create_channel_canvas(channel_id, md, title="Sales Handoff Checklist")
        kickoff_repo.mark_step_done(koid, "handoff_canvas")
    except Exception as e:  # noqa: BLE001 — best-effort; left un-done so a re-drive retries
        logger.warning("[kickstart] handoff canvas failed for %s: %s", koid, e)
        kickoff_repo.record_task_error(koid, "provisioning", "handoff_canvas", str(e))


def _finalize_tenant_context_canvas(koid, data, channel_id=None) -> None:
    """Render the sales→implementation context (fetched from f_stringer at register, stored in
    references.tenant_context) as a channel canvas — deferred + best-effort like the other canvases,
    idempotent via the tenant_context_canvas step flag."""
    if step_done(data, "tenant_context_canvas"):
        return
    md = (data.get("references") or {}).get("tenant_context")
    if not md:
        return
    channel_id = _channel_id_or_load(koid, channel_id)
    if not channel_id:
        return
    try:
        slack_client.create_channel_canvas(channel_id, md, title="Sales Context")
        kickoff_repo.mark_step_done(koid, "tenant_context_canvas")
        logger.info("[kickstart] sales context canvas posted for %s (%d chars) channel=%s", koid, len(md), channel_id)
    except Exception as e:  # noqa: BLE001 — best-effort; left un-done so a re-drive retries
        logger.warning("[kickstart] tenant context canvas failed for %s: %s", koid, e)
        kickoff_repo.record_task_error(koid, "provisioning", "tenant_context_canvas", str(e))


def post_tenant_created_slack(koid, data, tenant_id, body=None):
    """CREATE the internal Slack channel (int-<tenantId>-<subdomain>) and set it up — invite the
    support bot + the AE who registered, upload the order form + time-zone canvas, set the purpose
    + bookmarks, and post a 'Tenant Created' summary. Resilient: a Slack hiccup logs but never crashes
    completion (a missing channel surfaces as the provisioning 'create_channel' task showing failed).

    Returns the created channel_id (or None if this run didn't create one — already-done redelivery,
    dryrun, or a create failure) so completion can reuse it without re-SELECTing the kickoff row."""
    kd = KickoffData.from_blob(data)
    body = body or {}
    # Idempotent: skip the whole create + setup + summary if it already ran. WITHOUT this guard an
    # at-least-once SQS redelivery (or a manual re-drive) would re-create / re-post every time.
    if step_done(data, "tenant_created_slack"):
        return None
    if settings.dryrun_unconfigured and not (settings.kickstart_slack_bot_token and recipients.announce_channel):
        _dryrun("create_internal_channel", {"name": _internal_channel_name(tenant_id, data), "tenant": tenant_id})
        kickoff_repo.mark_step_done(koid, "tenant_created_slack")
        return None
    name = _internal_channel_name(tenant_id, data)
    # Channel setup is best-effort — a Slack hiccup must NOT strand a created tenant. create_channel is
    # idempotent (reuses on name_taken); on any other failure we log and leave it un-marked so a
    # re-drive retries (the create_channel task shows failed in the meantime).
    try:
        channel_id = slack_client.create_channel(name)
        kickoff_repo.set_slack_channel_config(koid, {"name": name, "channel_id": channel_id})
    except Exception as e:  # noqa: BLE001
        logger.warning("[kickstart] internal channel create failed for %s (%s) — leaving un-marked for re-drive", koid, e)
        return None
    # Join the channel + add the AE who registered the deal — best-effort, isolated so a lookup/invite
    # hiccup doesn't affect the rest.
    with best_effort("channel join", koid):
        slack_client.join_channel(channel_id)
    # Invite the standing Kickstart members + the AE who registered — best-effort, isolated per user so
    # one bad/deactivated id (or an already-member) doesn't block the rest.
    member_ids = list(recipients.slack_channel_members)
    ae_email = kd.registered_by
    if ae_email:
        with best_effort(f"AE lookup {ae_email}", koid):
            member_ids.append(slack_client.lookup_user_by_email(ae_email))
    for uid in dict.fromkeys(u for u in member_ids if u):  # dedupe, keep order
        with best_effort(f"add {uid} to channel", koid):
            slack_client.invite_to_channel(channel_id, uid)
    # Channel purpose + content: timezone in the purpose (folders aren't API-creatable), order-form PDF
    # (+ flat bookmark), then the "Tenant Created" summary. (The time-zone canvas is DEFERRED to the end of
    # run_completion_steps — a just-created channel's canvas backend isn't ready yet here.)
    with best_effort("tenant_created slack setup", koid):
        tz = kd.time_zone or "unknown"
        slack_client.set_channel_purpose(
            channel_id,
            f"Internal onboarding for {kd.company_name} · tenant {tenant_id} · "
            f"timezone {tz} · kickoff {koid} (Kickstart-managed).")
        order_form_link = _upload_order_form(koid, channel_id, kd.company_name)
        if isinstance(order_form_link, str) and order_form_link.startswith("http"):
            with best_effort("order form bookmark", koid):
                slack_client.add_bookmark(channel_id, "Order Form", link=order_form_link, emoji=":page_facing_up:")
        monday = body.get("mondayWorkspace") if isinstance(body, dict) else None
        lines = [
            ":white_check_mark: *Tenant Created*",
            f"Tenant ID: {tenant_id}",
            f"Tenant: {kd.company_name}",
        ]
        if monday:
            lines.append(f"Monday workspace: {monday}")
        slack_client.post_message(channel_id, "\n".join(lines))
    # Channel bookmarks — flat (Slack API can't create folders): Product Instance + Kickstart app link.
    with best_effort("tenant_created bookmarks", koid):
        subdomain = kd.domain_name
        if subdomain:
            site_url = f"https://{subdomain}.{constants.TENANT_SITE_DOMAIN}"
            slack_client.add_bookmark(channel_id, "Product Instance", link=site_url, emoji=":globe_with_meridians:")
        slack_client.add_bookmark(channel_id, "Kickstart App Link", link=_kickoff_detail_url(koid), emoji=":rocket:")
    # Mark done LAST so the create + setup + summary + bookmarks run exactly once (redelivery / re-drive skips).
    kickoff_repo.mark_step_done(koid, "tenant_created_slack")
    return channel_id


def _parent_linear_id(data):
    """The KO org issue id from the in-hand data['references'] (no DB reload). Only a REAL id is
    usable as a parent — a simulated 'sim-' id → None."""
    ref = (data.get("references") or {}).get("kickoff_linear_id")
    return ref if ref and not str(ref).startswith("sim-") else None


def update_linear_tid(koid, data, tenant_id):
    """At provisioning: inject the tenant ID + internal Slack channel name into the KO org-issue
    description (the channel exists by now — created just before completion). Best-effort."""
    if settings.dryrun_unconfigured and not settings.linear_api_key:
        _dryrun("update_linear_tid", {"koid": koid, "tenant_id": tenant_id})
        return
    pid = _parent_linear_id(data)
    if not pid:
        return
    with best_effort("update_linear_tid", koid):
        channel = _internal_channel_name(tenant_id, data)
        linear_client.update_issue(pid, _kickoff_desc(koid, data, tenant_id, channel_name=channel))


# Academy-access table (MARKT-2843): columns defined ONCE as (header, cell-from-user). Header row,
# separator, and data rows all derive from this — the column contract can't drift.
_ACADEMY_COLUMNS = [
    ("First Name", lambda u: u.get("first_name") or ""),
    ("Last Name", lambda u: u.get("last_name") or ""),
    ("Email", lambda u: u.get("email") or ""),
    ("Academy Manager Name (Onboarding/Tenant PoC)", lambda u: constants.LINEAR_ACADEMY_MANAGER),
    ("Course to assign (v2 or v3 or v2--> v3 )", lambda u: constants.LINEAR_ACADEMY_COURSE),
]


def _academy_desc(kd) -> str:
    """Academy-access Linear body — the MARKT-2843 template: a table of the customer's academy users
    plus the cc-praveen mention. Caller guarantees kd.academy_users is non-empty."""
    lines = ["| " + " | ".join(h for h, _ in _ACADEMY_COLUMNS) + " |",
             "| " + " | ".join("--" for _ in _ACADEMY_COLUMNS) + " |"]
    lines += ["| " + " | ".join(fn(u) for _, fn in _ACADEMY_COLUMNS) + " |" for u in kd.academy_users]
    return "\n".join(lines) + f"\n\n{constants.LINEAR_ACADEMY_CC}"


def _create_academy_linear(koid, data):
    """Post-tenant: Academy access request under the KO parent (assignee Wayne), body = the MARKT-2843
    template table of the customer's academy users. Urgent + Todo. Skipped when no academy users.
    Best-effort."""
    kd = KickoffData.from_blob(data)
    if not kd.academy_users:
        return
    if (data.get("references") or {}).get("academy_linear_url"):
        return  # already created (ref-stored checkpoint) — redelivery skip
    if settings.dryrun_unconfigured and not settings.linear_api_key:
        _dryrun("create_academy_linear", {"tenant": kd.company_name})
        kickoff_repo.add_reference(koid, "academy_linear_url", _sim_linear_url(koid, "academy"))
        return
    try:
        issue = linear_client.create_issue(
            recipients.marketing_team, f"Academy Access Request for {kd.company_name}",
            description=_academy_desc(kd), assignee_id=recipients.linear_wayne,
            parent_id=_parent_linear_id(data),
            priority=constants.LINEAR_PRIORITY_URGENT, state_id=recipients.marketing_state_id)
        kickoff_repo.add_reference(koid, "academy_linear_url", issue.url)
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning("[kickstart] academy linear failed for %s: %s", koid, e)
        # Surface the cause so a silent best-effort failure is observable (task shows "failed").
        kickoff_repo.record_task_error(koid, "provisioning", "academy_linear", str(e))


def _create_connector_linears(koid, data):
    """Post-tenant: one Linear per requested connector (free-text entries [{connector_name, context?}]),
    assigned to Praneeth. Skipped when no connectors. Best-effort per connector."""
    kd = KickoffData.from_blob(data)
    connectors = kd.connectors
    if not connectors:
        return
    if (data.get("references") or {}).get("connector_linear_urls"):
        return  # already created (ref-stored checkpoint) — redelivery skip
    tid = kd.tenant_id
    tenant = kd.company_name
    if settings.dryrun_unconfigured and not settings.linear_api_key:
        urls = {(c.get("connector_name") or f"connector-{i}"): _sim_linear_url(koid, str(i), prefix="CON2")
                for i, c in enumerate(connectors)}
        _dryrun("create_connector_linears", {"connectors": [c.get("connector_name") for c in connectors]})
        kickoff_repo.add_reference(koid, "connector_linear_urls", urls)
        return
    urls = {}
    errors = []
    for c in connectors:
        cname = c.get("connector_name")
        if not cname:
            continue
        ctx = (c.get("context") or "").strip()
        try:
            # Matches the Connectors-team convention: "T<tenantId> <Tenant>: <Source>".
            desc = f"Connector setup for {tenant} (tenant T{tid}, ko-id {koid})."
            if ctx:
                desc += f"\n\nContext: {ctx}"
            issue = linear_client.create_issue(
                recipients.connectors_team, f"T{tid} {tenant}: {cname}",
                description=desc, assignee_id=recipients.linear_praneeth,
                priority=constants.LINEAR_PRIORITY_URGENT, state_id=recipients.connector_state_id)
            urls[cname] = issue.url
        except Exception as e:  # noqa: BLE001 — best-effort per connector
            logger.warning("[kickstart] connector linear failed for %s/%s: %s", koid, cname, e)
            errors.append(f"{cname}: {e}")
    if urls:
        kickoff_repo.add_reference(koid, "connector_linear_urls", urls)
    # Surface the cause so a silent best-effort failure is observable (task shows "failed", not a green tick).
    if errors and not urls:
        kickoff_repo.record_task_error(koid, "provisioning", "connector_linears", "; ".join(errors))


def _notify_paaras(koid, data):
    if step_done(data, "paaras_dm"):
        return "skipped: already notified"  # boolean-flag checkpoint — redelivery skip (no dup DM)
    if not recipients.paaras_slack_id:
        logger.info("[kickstart] notify_paaras skipped — Paaras DM not configured (deferred)")
        return "skipped: paaras_slack_id not configured"
    # Link to the kickoff DETAIL page — the LD-gated inline "Assign consultant" action lives
    # there now (no standalone /assign page).
    kd = KickoffData.from_blob(data)
    detail_url = _kickoff_detail_url(koid)
    msg = f"New tenant ready for {koid} ({kd.company_name}) — assign a consultant: {detail_url}"

    def _send():
        return slack_client.send_dm(recipients.paaras_slack_id, msg)  # raises → not marked → retry

    # raises on failure → run_step leaves paaras_dm un-done; the async handler records last_error
    # and SQS redelivers (so no duplicate DM, and the retry resumes).
    return run_step(koid, data, "paaras_dm", _send)


def _monday_workspace_url(workspace_id) -> str:
    return f"https://{constants.MONDAY_ACCOUNT_SLUG}.monday.com/workspaces/{workspace_id}"


def _create_monday_workspace(koid, data, channel_id=None):
    """Provisioning: create the per-tenant Monday workspace 'T<tenantId> <Company>' and add the base
    owners (Paaras, Jason, Ankit — env-resolved via recipients). Best-effort (like the academy /
    connector Linears): a Monday hiccup logs but never strands a created tenant in PROVISIONING — the
    step stays un-done and is recoverable via reset(PROVISIONING). Idempotent + resumable: the
    workspace id is stashed in references.monday_workspace_id right after create (also recovered by
    name), so a re-drive reuses it (no duplicate workspace) and base owners are re-added idempotently.
    Guarded by the provision_monday_workspace step flag."""
    kd = KickoffData.from_blob(data)
    def _body():
        name = f"T{kd.tenant_id} {kd.company_name or 'Customer'}"
        owners = recipients.monday_workspace_owners
        refs = data.get("references") or {}
        wid = refs.get("monday_workspace_id") or monday_client.find_workspace_by_name(name)
        if not wid:
            wid = monday_client.create_workspace(name, description=f"Drivetrain kickoff {koid} — {kd.company_name or 'Customer'}")
        # Persist the id immediately so a re-drive reuses it (no duplicate workspace) before owners run.
        kickoff_repo.merge_references(koid, {
            "monday_workspace_id": wid,
            "monday_workspace_url": _monday_workspace_url(wid),
        })
        # Resolve the base owners ONCE and reuse: owner_ids seeds create_board's board_owner_ids, and
        # the same map is handed to add_owners so it doesn't re-run the users(emails:) lookup.
        owner_map = monday_client.resolve_user_map(owners)
        owner_ids = list(owner_map.values())
        result = monday_client.add_owners(wid, owners, user_map=owner_map)
        # Template board inside the workspace (idempotent: ref id or find-by-name), base owners at creation.
        board_id = refs.get("monday_board_id") or monday_client.find_board_by_name(wid, constants.MONDAY_BOARD_TEMPLATE_NAME)
        if not board_id:
            board_id = monday_client.create_board_from_template(
                wid, constants.MONDAY_BOARD_TEMPLATE_NAME, constants.MONDAY_BOARD_TEMPLATE_ID,
                owner_ids=owner_ids, kind=constants.MONDAY_BOARD_KIND)
        kickoff_repo.merge_references(koid, {"monday_board_id": board_id})
        # Flat channel bookmark for the workspace link (mirrors the manual "Monday Project Tracker").
        _add_channel_bookmark(koid, "Monday Project Tracker", _monday_workspace_url(wid),
                              emoji=":chart_with_upwards_trend:", channel_id=channel_id)
        return f"monday workspace {wid} + board {board_id} ('{name}') ready — {result}"
    company = kd.company_name or "Customer"
    dry = settings.dryrun_unconfigured and not settings.monday_api_key
    return run_step(koid, data, "provision_monday_workspace", _body,
                    skip_result="skipped: workspace already provisioned",
                    dryrun_when=dry,
                    dryrun_result=(_dryrun("create_monday_workspace", {"name": f"T{kd.tenant_id} {company}", "owners": list(recipients.monday_workspace_owners)}) if dry else None),
                    best_effort=True, task_key="monday_workspace", stage="provisioning")


def run_completion_steps(koid, data, channel_id=None) -> dict:
    """Provisioning completion (post-tenant): Monday workspace → academy + connector Linears → inject
    tenant ID into the parent → Paaras DM. The internal Slack channel is created just BEFORE this (see
    post_tenant_created_slack), and the Linear template shell (parent + gap TODOs + Heimdall) was
    created at register; the Monday workspace + academy/connector handoffs are added here, after Drive
    execution (the workspace name needs the tenant id). `channel_id` is the id post_tenant_created_slack
    just created — threaded through so the channel-bookmark + timezone-canvas steps don't re-SELECT the
    kickoff row (they fall back to a DB read when it's absent, e.g. a redelivery that skipped create)."""
    kd = KickoffData.from_blob(data)
    _create_monday_workspace(koid, data, channel_id=channel_id)
    _create_academy_linear(koid, data)
    _create_connector_linears(koid, data)
    update_linear_tid(koid, data, kd.tenant_id)
    _notify_paaras(koid, data)
    # Canvas LAST — deferred here on purpose: the Monday/Linear work above buys the just-created channel
    # enough time for Slack to provision its canvas backend, so this succeeds first-try (vs failing mid
    # channel-setup with canvas_tab_creation_failed). See _finalize_timezone_canvas.
    _finalize_timezone_canvas(koid, data, channel_id=channel_id)
    _finalize_hubspot_canvas(koid, data, channel_id=channel_id)  # 2nd canvas tab — same deferred, best-effort, idempotent path
    _finalize_handoff_canvas(koid, data, channel_id=channel_id)  # 3rd canvas tab — Sales Handoff checklist, same path
    _finalize_tenant_context_canvas(koid, data, channel_id=channel_id)  # 4th canvas tab — sales→implementation context
    return {}
