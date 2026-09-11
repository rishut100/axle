"""Stage / task progress for kickoff visibility — DERIVED on read (hybrid).

Happy-path task status (done / pending / skipped) + the 6-stage timeline are computed
from the kickoff's observable state — no stored ledger, no per-step write churn, no drift.
The only stored signals are:
  - data["references"]: produced links/ids {monday_workspace_url, kickoff_linear_url, ...}  (kickoff_repo.add_reference)
  - data["task_errors"]: {"<stage>:<key>": message}  written ONLY on a task failure (kickoff_repo.record_task_error)

A task with a recorded error shows "failed"; everything else derives from status + state.
"""

# Status → rank (drives "is this stage done/current/upcoming").
# registering=1 is the earliest status: the row exists but the async register work is in flight.
# The sales_registry stage renders "current" at rank 1 and "done" from rank 2 (past REGISTERING).
_STATUS_RANK = {
    "registering": 1,
    "awaiting_customer_response": 2,
    "provisioning": 3,
    "awaiting_consultant": 4,
    "consultant_assigned": 5,  # intermediate: post-assign work (Monday owners + channel-add) in flight
    "done": 6,
}

STAGE_ORDER = [
    "sales_registry",
    "awaiting_customer_response",
    "provisioning",
    "awaiting_consultant",
    "consultant_assigned",
    "done",
]

STAGE_LABELS = {
    "sales_registry": "Sales Registry",
    "awaiting_customer_response": "Awaiting Customer Response",
    "provisioning": "Provisioning",
    "awaiting_consultant": "Awaiting Consultant Assignment",
    "consultant_assigned": "Consultant Assigned",
    "done": "Done",
}

# stage → [(task_key, label)]. Only the three work-stages carry tasks.
TASK_CATALOG = {
    "sales_registry": [
        ("gtm_post", "Post in #gtm-process-commercial-kickoff"),
        ("demo_cleanup", "Remove users from POC tenant"),
        ("linear_template", "Create Linear kickoff template (parent + sub-issues)"),
        ("sales_context", "Generate sales context from pre-sales calls"),
        ("requisition_email", "Send requisition form email to customer"),
    ],
    "provisioning": [
        ("create_tenant", "Create tenant + store id"),
        ("create_channel", "Create internal Slack channel"),
        ("monday_workspace", "Create Monday workspace + add Paaras, Jason, Ankit"),
        ("academy_linear", "Academy-access Linear — Jason"),
        ("connector_linears", "Connector Linears — Praneeth, one per connector"),
        ("linear_tid", "Update Linear template with tenant ID"),
        ("assign_nudge", "Slack → Paaras to assign consultant"),
    ],
    "consultant_assigned": [
        ("monday_owners", "Add consultant / analyst / pod-lead as Monday owners"),
        ("channel_add", "Add assigned folks to the int-* channel"),
    ],
}

# Mode is derived, not stored: a wired task shows a "dry-run" chip when its integration key isn't
# configured (live["linear"]).
_INTEGRATION = {
    ("sales_registry", "gtm_post"): "slack",
    ("sales_registry", "linear_template"): "linear",
    ("provisioning", "monday_workspace"): "monday",
    ("provisioning", "academy_linear"): "linear",
    ("provisioning", "connector_linears"): "linear",
    ("provisioning", "linear_tid"): "linear",
    ("consultant_assigned", "monday_owners"): "monday",
}


def _mode(stage, key, live):
    """None = real; 'dryrun' = wired but the integration key isn't configured (runs simulated)."""
    integ = _INTEGRATION.get((stage, key))
    if integ and not live.get(integ):
        return "dryrun"  # wired, but the integration key isn't configured
    return None

# stage_key → (done when rank >= D, current when rank == C). C=None → never "current".
_STAGE_STATE = {
    "sales_registry": (2, 1),                     # current while REGISTERING (rank 1); done once past it
    "awaiting_customer_response": (3, 2),
    "provisioning": (4, 3),
    "awaiting_consultant": (5, 4),
    "consultant_assigned": (6, 5),                # current while CONSULTANT_ASSIGNED (post-assign work in flight); done at DONE
    "done": (6, None),
}

_LABEL = {(s, k): lbl for s, tasks in TASK_CATALOG.items() for k, lbl in tasks}


# ── Derivation (read-time) ──

def compute_stages(rec: dict, status_log: list) -> list:
    """Build the 6-stage timeline (each with state + entered_at + derived tasks) from a
    kickoff record. rec keys: status, created_at, tenant_id, provisioning_done_at,
    poc_tenant_id, connectors, academy_users, has_consultant, references, task_errors."""
    status = rec.get("status")
    # A FAILED kickoff has no rank of its own — derive it from the stage it failed in (the last
    # non-'failed' status_log entry) so prior stages still render done and the failed stage is flagged.
    failed = status == "failed"
    if failed:
        prior = next((e.get("status") for e in reversed(status_log or []) if e.get("status") != "failed"), None)
        rank = _STATUS_RANK.get(prior, 0)
    else:
        rank = _STATUS_RANK.get(status, 0)
    refs = rec.get("references") or {}
    errs = rec.get("task_errors") or {}
    tenant_id = rec.get("tenant_id")
    poc_tid = rec.get("poc_tenant_id")
    # Slack channel signal (honest derivation): channel_id present → the internal channel was actually
    # created post-tenant (completion stored it). The channel is created ONCE in v3 (no rename step).
    channel_id = rec.get("channel_id")
    connectors = rec.get("connectors") or []
    academy = rec.get("academy_users") or []
    has_consultant = bool(rec.get("has_consultant"))
    # rank>=4 (awaiting_consultant) is the steady-state signal; provisioning_done_at also covers the
    # brief window where the tenant is created (completion ran) but status hasn't advanced yet.
    provisioned = rank >= 4 or bool(rec.get("provisioning_done_at"))
    live = rec.get("live") or {}

    def task(stage, key, status_val, ref=None):
        err = errs.get(f"{stage}:{key}")
        status = "failed" if err else status_val
        return {
            "stage": stage, "key": key, "label": _LABEL.get((stage, key), key),
            "status": status,
            "mode": _mode(stage, key, live),
            "ref": ref, "error": err,
        }

    def gate(items):
        """List-gated provisioning task: skipped if empty, else done/pending by provisioned."""
        return "skipped" if not items else ("done" if provisioned else "pending")

    # The register-ancillary work runs async while REGISTERING (rank 1); once it completes the status
    # advances to awaiting_customer_response (rank >= 2). Tasks show done from rank >= 2 (or earlier
    # if their ref already landed mid-flight); pending while still REGISTERING.
    registered = rank >= 2
    reg = lambda ref=None: "done" if (registered or ref) else "pending"  # noqa: E731
    by_stage = {
        "sales_registry": [
            # honest: done only when the announce actually posted (gtm_announce ref present); a
            # registered kickoff with no ref means the post failed/was skipped → failed, not a tick.
            task("sales_registry", "gtm_post",
                 "done" if refs.get("gtm_announce") else ("failed" if registered else "pending"),
                 refs.get("gtm_announce")),
            task("sales_registry", "demo_cleanup", ("done" if registered else "pending") if poc_tid else "skipped"),
            task("sales_registry", "linear_template", reg(refs.get("kickoff_linear_url")), refs.get("kickoff_linear_url")),
            # done when the brief landed; failed on a recorded fetch error; else skipped (no calls).
            task("sales_registry", "sales_context",
                 "done" if refs.get("tenant_context") else ("pending" if not registered else "skipped")),
            task("sales_registry", "requisition_email", reg(refs.get("intake_link")), refs.get("intake_link")),
        ],
        "provisioning": [
            task("provisioning", "create_tenant", "done" if tenant_id is not None else "pending",
                 str(tenant_id) if tenant_id is not None else None),
            # honest: the internal channel is created post-tenant — done once channel_id is stored;
            # tenant created (or provisioning ran) but no channel yet → failed; not provisioned → pending.
            task("provisioning", "create_channel",
                 "done" if channel_id else ("failed" if (tenant_id or provisioned) else "pending")),
            task("provisioning", "monday_workspace",
                 "done" if refs.get("monday_workspace_url") else "pending", refs.get("monday_workspace_url")),
            task("provisioning", "academy_linear", gate(academy), refs.get("academy_linear_url")),
            task("provisioning", "connector_linears", gate(connectors), refs.get("connector_linear_urls")),
            task("provisioning", "linear_tid", "done" if tenant_id is not None else "pending",
                 refs.get("kickoff_linear_url")),
            task("provisioning", "assign_nudge", "done" if rank >= 4 else "pending"),
        ],
        "consultant_assigned": [
            # post-assign work runs async while CONSULTANT_ASSIGNED (rank 5); done once it completes (status DONE, rank >= 6).
            task("consultant_assigned", "monday_owners", "done" if (rank >= 6 and has_consultant) else "pending"),
            # honest: no channel to add to → skipped (not a green tick); done only once the
            # post-assign work ran (rank>=6) AND a channel actually exists.
            task("consultant_assigned", "channel_add",
                 "skipped" if (rank >= 6 and not channel_id)
                 else ("done" if (rank >= 6 and has_consultant and channel_id) else "pending")),
        ],
    }

    entered = {e.get("status"): e.get("created_at") for e in (status_log or [])}
    out = []
    for skey in STAGE_ORDER:
        done_at, current_at = _STAGE_STATE[skey]
        state = "done" if rank >= done_at else ("current" if current_at is not None and rank == current_at else "upcoming")
        if failed and state == "current":
            state = "failed"  # the stage the kickoff failed in
        # sales_registry has no status_log entry of its own → uses the row's created_at; every
        # other stage keys its "entered_at" off the same-named status_log entry.
        entered_at = rec.get("created_at") if skey == "sales_registry" else entered.get(skey)
        out.append({
            "key": skey,
            "label": STAGE_LABELS[skey],
            "state": state,
            "entered_at": entered_at,
            "tasks": by_stage.get(skey, []),
        })
    return out
