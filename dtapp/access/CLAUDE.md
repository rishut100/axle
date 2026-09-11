# Access automation (axle backend) — ENG-90754

Flask backend that replaces the manual `#tool-access-poc` Slack process: on a Keka hire/leaver event,
automatically grants/revokes tool access via each tool's admin API where one exists, and falls back to
an auto-created Linear ticket (assigned to the tool's human owner) for tools with no API. Deliberately
mirrors `dtapp/garage/kickstart/` — read that module's CLAUDE.md first if you haven't; this doc only
covers what's different.

## Directory structure

```
dtapp/access/
├── api/access_api.py         # POST /access/webhook/keka (primary trigger), /event (manual),
│                              #   /event/<id>, /event/<id>/tool/<tool>/complete, matrix CRUD
├── schemas/access_event.py   # NormalizedAccessEvent + from_keka_payload (the Keka-field-name boundary)
├── services/
│   ├── event_service.py      # ingest_event: validate → resolve matrix → write row + outbox (1 txn)
│   └── verify_service.py     # offboarding drift-check (cron, NOT a queued message — see below)
├── matrix/matrix_repo.py     # role→tool→owner matrix (DB table, admin-editable via the API above)
├── access_steps/
│   ├── _runner.py            # run_step — copy of kickstart's, wired to access_event_repo instead
│   ├── common.py             # CLIENT_REGISTRY-equivalent (get_client) — add a tool here, not in grant/revoke
│   ├── grant.py / revoke.py  # iterate the checklist, dispatch per tool, best-effort per entry
├── repositories/access_event_repo.py  # AccessEvent table (JSONB checklist)
├── clients/
│   ├── scim_client.py        # generic SCIM 2.0 client — covers most tools via matrix client_config
│   └── github_client.py      # example bespoke (non-SCIM) client
├── recipients.py, notify.py, constants.py, enums.py, status_machine.py
└── sqs.py, queue.py          # dedicated "garage-access" SQS queue + consumer
```

## Lifecycle & statuses

`pending → in_progress → done` (+ `partial_failed`, re-drivable back to `in_progress`). One
`AccessEvent` row per (person, onboard|offboard action); `checklist` (JSONB) holds one entry per tool
resolved from the matrix for that person's (team, role). A grant/revoke pass never blocks on a single
tool's failure — every OTHER tool in the checklist still gets attempted (`access_steps._runner.run_step`
with `best_effort=True`).

## Key patterns (deltas from kickstart)

1. **Trigger = Keka webhook only in v1.** Axle's existing `/okta/*` webhooks are wired to the
   **customer-facing product Okta** (per-tenant SaaS SSO), not Drivetrain's own corporate IdP — don't
   assume they're reusable here. See `schemas/access_event.py::from_keka_payload` for the
   payload-shape isolation boundary; **its field-name mapping is a best guess pending a real Keka
   webhook payload sample** — confirm and adjust before go-live.
2. **Matrix, not code, decides the checklist.** `access_tool_matrix` (DB table) maps
   `(team, role) -> [{tool, method: api|manual, client_name, client_config, owner}]`. Add/remove a
   tool via the `/access/matrix` API, never by editing `grant.py`/`revoke.py`.
   `client_config` currently stores SCIM tokens in plaintext JSONB — fine for the current tool count,
   flagged as a TODO to move to Secrets Manager once the matrix grows past a handful of entries.
3. **Dedicated queue, shared outbox table.** `access/sqs.py`/`queue.py` are a deliberate near-copy of
   kickstart's, not a shared/parameterized module — this may become its own service later, same as
   kickstart. The `outbox` table IS shared (its `type` column already exists to discriminate message
   kinds); `outbox_repo.list_pending`'s relay backstop takes a `types` filter specifically so this
   module's stuck rows can never be relayed onto KICKSTART's queue or vice versa — if you add a new
   `Outbox.type` value anywhere in this repo, make sure both modules' relay calls stay scoped to their
   own types (`access/constants.py::OUTBOX_TYPES`).
4. **One generic `ScimClient`**, config-driven per matrix row, covers most tools. Only write a bespoke
   client (`clients/<tool>_client.py`, registered in `access_steps/common.py::get_client`) for a
   genuine non-SCIM outlier — GitHub's org-membership REST API (`clients/github_client.py`) and
   corporate Okta (`clients/okta_client.py`, the "Drivetrain" row's client — see its docstring for why
   this is a DIFFERENT Okta org than `views/okta.py`'s customer-facing one) are the two built so far.
   Bespoke clients expose email-based `invite_member`/`remove_member`/`get_user_status` (the dispatch
   in `grant.py`/`revoke.py`/`verify_service.py` checks `hasattr(client, "invite_member")` to route
   bespoke vs. SCIM); ScimClient exposes id-based `create_user`/`deactivate_user`/`get_user_status`.
5. **Manual-tool "done" = a Slack ✅ reaction, which ALSO closes the Linear ticket.** The Linear
   sub-issue (`access_steps/grant.py::_grant_manual_tool`) is created with its `linear_team_id` stashed
   on the checklist entry; the reaction handler (`dtapp/views/slack.py::_handle_access_event_reaction`,
   extended alongside its existing `tenant_access_request` handling) and the `POST
   /access/event/<id>/tool/<tool>/complete` backstop both update our DB AND best-effort call
   `linear_client.close_issue(issue_id, team_id)` (looks up the team's `completed`-type workflow state
   at runtime, cached per team). A Linear-side failure never undoes the DB completion already recorded.
6. **Both directions get post-action verification, not just offboarding.** `run_revoke_verification`
   (offboard) and `run_grant_verification` (onboard, added after real-world testing surfaced the gap —
   grant-verify runs `GRANT_VERIFY_DELAY_HOURS` after dispatch, shorter than revoke's, since an
   unconfirmed "you have access" is worse to leave open than an unconfirmed revoke) share
   `verify_service._check_one_tool`, which resolves the right identifier per client type
   (`_resolve_identifier`: SCIM → stored `scim_user_id`; bespoke → email). Grant drift goes to the
   routine logs channel (an availability problem); revoke drift goes to the dedicated security channel
   (`recipients.drift_alert_channel`) — both are CRON DB-scans (`REVOKE_VERIFY_CRON`/`GRANT_VERIFY_CRON`
   on the shared `CronScheduler` in `main.py`), not queued SQS messages, since they need to survive well
   past any single message's visibility timeout and re-run on a schedule.

## Development

No test framework in this repo (matches the rest of axle) — verify with `python -m py_compile` on new
files plus a local `curl` against the webhook/event routes. With `keka_webhook_secret`,
`github_api_token`, and every matrix row's SCIM token blank, every step dry-runs (logs + marks done)
rather than failing loud — same `dryrun_unconfigured` convention as kickstart.

## Gotchas

1. **Checklist entries carry the whole context** (`employee_email`/`employee_name`/`team`) so a step
   never needs an extra DB round-trip — but that also means editing the matrix AFTER an event's
   checklist is built does NOT retroactively change that event; only future events see the new matrix.
2. **JSONB writes** — reassign the dict, never mutate in place (same as kickoff_repo; SQLAlchemy's
   dirty-check misses an in-place mutation and the write silently drops).
3. **`external_id` is the idempotency key**, not `aeid` — a re-delivered Keka webhook for the same
   person+action must resolve to the SAME AccessEvent, not create a duplicate.
4. **Dead-lettering is per-MESSAGE (per grant/revoke pass), not per-tool** — a dead-lettered event's
   checklist can be a mix of granted/failed/pending tools; check the checklist, not just the top-level
   status, before assuming "dead-lettered" means "nothing happened."
