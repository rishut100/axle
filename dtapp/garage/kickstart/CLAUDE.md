# Kickstart (axle backend)

Flask backend for the customer **kickstart** flow — the counterpart to `garage-fe` (the SPA). Lifecycle:
**register a sale → public customer intake → provisioning → consultant handoff**. All routes are under
`/garage/kickstart/*` (a Flask blueprint, `kickstart_bp`). Slow external work (Linear/Slack/Monday/Drive/
SendGrid) never runs in the request — it's enqueued to SQS and run by async consumers.

## Directory Structure

```
dtapp/garage/kickstart/
├── api/
│   ├── kickoff_api.py       # THE routes (blueprint). parse_body() + @require_drivetrainer guard.
│   ├── openapi.py           # non-prod OpenAPI spec + Swagger UI (/garage/kickstart/docs)
│   └── check_openapi_drift.py
├── schemas/
│   ├── kickoff.py           # pydantic DTOs (RegisterPayload groups, IntakeSubmission, AssignPayload,
│   │                        #   KickoffData stored-blob, flatten_groups, EDITABLE_FIELDS, _normalize_url)
│   └── clients.py, drive.py # client response models (LinearIssue, …)
├── services/                # request-thin orchestration (validate + repo + enqueue; NO external work)
│   ├── kickoff_service.py   # create/update_draft, submit_draft, get_detail/serialize_detail, update_fields
│   ├── intake_service.py    # submit_intake (token → provisioning enqueue)
│   ├── assign_service.py    # assign_consultant (FKs + re-assign diff + enqueue)
│   └── consultant_service.py
├── kickoff_steps/           # the ASYNC side-effect steps (run by the SQS consumers)
│   ├── register.py          # run_register_steps: cleanup_demo_users → _create_linear_tree → gtm_announce
│   ├── completion.py        # run_completion_steps: create channel → Monday → academy/connector Linears →
│   │                        #   canvases (tz / hubspot / handoff) → notify Paaras
│   ├── assign.py            # run_assign_steps: channel_add → monday_owners → deck_linear
│   ├── _runner.py           # run_step / step_done / dryrun — idempotent, best-effort step wrapper
│   ├── tasks.py             # TASK_CATALOG + compute_stages (the progress timeline the FE renders)
│   └── common.py            # shared helpers (_resolve_channel_id, …)
├── repositories/            # SQLModel data access (the ONLY place that touches the DB)
│   ├── kickoff_repo.py      # Kickoff table + get_kickoff/get_draft, *_atomic writes, references/steps
│   ├── consultant_repo.py, outbox_repo.py
├── clients/                 # external APIs via core.http_util.ServiceClient (sync requests)
│   ├── __init__.py          # re-exports the singletons: linear_client, slack_client, monday_client, drive_client, stringer_client
│   └── linear_client.py, slack_client.py, monday_client.py, drive_client.py, stringer_client.py
├── recipients.py            # env-resolved routing (teams / assignees / channels / notify+logs) — ONE switch point
├── notify.py                # Slack helpers — @-mentions (mention / ae_mention) + send_log (Kickstart Logs)
├── reminders.py             # cron scans: run_intake_reminders + run_consultant_reminders (idempotent per day)
├── token.py                 # intake capability token (public /intake/<token>)
└── sqs.py                   # the kickstart provisioning queue
```

## Lifecycle & statuses

`registering → awaiting_customer_response → provisioning → awaiting_consultant → consultant_assigned → done`
(+ `failed`). Register + provision + assign each enqueue an outbox intent → published to SQS → an async
consumer runs `run_*_steps`. The FE polls `GET /kickoff/<koid>`; `tasks.py compute_stages` derives the
per-stage task timeline from `references` + `completed_steps` + `task_errors`.

## Key patterns

### 1. Request-thin services + async steps
- Routes (`api/kickoff_api.py`) validate via `parse_body(Model)` (pydantic → 400 on invalid) and call a
  service. Services do the DB write + `flush_outbox(koid)` — **no external calls in the request**.
- The SQS consumer runs `kickoff_steps/run_*_steps`. Each step is wrapped by `run_step(koid, data, "<flag>",
  body, …)`: **idempotent** (skips if the step flag is set — `step_done`/`mark_step_done`) and re-runnable
  on SQS redelivery. Local (no token/key) → `dryrun` short-circuits.

### 2. Stored data shape (`kickoff.data`, JSONB)
- Stored **grouped**: `{company_details, deal_details, sales_handoff, + top-level intake/workflow keys}`.
  `flatten_groups(data)` lifts the register groups to top-level for flat reads (+ maps `sales_handoff` →
  `handoff_checklist`). `RegisterPayload` IS the grouped shape; `KickoffData.from_blob` is the typed view.
- **Always reassign nested JSONB, never mutate in place** — SQLAlchemy won't detect an in-place dict
  mutation (`k.data = {**k.data, …}`). See `add_reference` / `merge_references` (merge, don't overwrite),
  `set_slack_channel_config`, `_mutate`.
- `references` = produced ids/links (`kickoff_linear_id`, `deck_linear_id`, `academy_linear_url`,
  `monday_workspace_id`, …). `slack_channel_config` = a **top-level column** `{name, channel_id}` (set at
  completion). `completed_steps` = step flags; `task_errors` = `"<stage>:<key>" → msg`.

### 3. Best-effort + observability
- External steps are **best-effort** — a failure must never strand the flow. Two shapes:
  - **Retry-safe, catalog-tracked**: `run_step(..., best_effort=True, task_key=..., stage=...)` — run_step
    catches, `record_task_error` (shows as a "failed" task), leaves the step un-done so redelivery retries
    (e.g. `_monday_owners`).
  - **Swallow + record, no retry**: catch internally, `record_task_error`, don't raise — for
    non-idempotent / deterministic-failure work where a retry would spam or DLQ (e.g. `_create_academy_linear`,
    the deck reassign+ping).

### 4. `recipients.py` — env-resolved routing (single switch point)
- `recipients = _PROD if settings.is_prod else _NONPROD`. PROD → real people/teams/channels; every non-prod
  env → Shahbaz + the "Eng Dev Testing" Linear bucket + `#shahbaz-dev-test` (never pings real folks).
- Linear teams: `linear_team` (Kickoff, KIC-), `connectors_team` (CON2-), `marketing_team` (MARKT-, the
  Academy access request). Each team pairs with its own `*_state_id` ("Todo").

### 5. Editable AE fields (Round 3)
- `PATCH /garage/kickstart/kickoff/<koid>` → `kickoff_service.update_fields`: whitelist against
  `EDITABLE_FIELDS`, **merge the changes onto the stored register groups, then re-validate the WHOLE via
  `RegisterPayload`** (`merge_and_validate_edit`) so every cross-field rule holds. Record-only — no
  downstream propagation. Replaced the old `/kickoff-date` route.

### 6. Linear tree + consultant automation (Round 3)
- `_create_linear_tree` (register): parent `Kickoff: <Customer>` → Jason, + sub-issues (Urgent/Todo). Skips
  the "Set up external Slack channel" sub-issue when the AE already supplied one. The **"Update Kickoff
  Deck"** sub-issue's id/url is stashed in `references` (keyed off `_DECK_SUBISSUE_TITLE`).
- Academy Access Request (completion) is created in the **Marketing team** as a child of the parent.
- Sales-Handoff checklist is posted as a Slack **canvas** at completion (deferred to the end, like the
  time-zone canvas).
- On `/assign` (`assign.py _deck_linear`): reassign the Update-Kickoff-Deck sub-issue to the consultant +
  ping them in the internal channel with the issue link. Re-runs on re-assignment (flag cleared in
  `assign_consultant_atomic`).

### 7. Reminders & alerts (Phase 3)
- Two daily cron scans (registered in `main.py` on the shared `CronScheduler`; timings in
  `core/constants.py`): `reminders.run_intake_reminders` (AE on Slack + customer on the threaded intake
  email, while `awaiting_customer_response`) and `run_consultant_reminders` (Paaras in the kickoff's own
  channel, from `kickoff_date - CONSULTANT_REMINDER_LEAD_DAYS`, while `awaiting_consultant`).
- Idempotent per `(kickoff, day)` via the `reminders` JSONB column (`merge_reminders`) — double-fire /
  pod-restart safe. Channels are env-resolved (`recipients.notify_channel` / `logs_channel`; non-prod → Shahbaz Dev).
- Email threading: `sendgrid_client` stamps a deterministic `Message-ID` on the invite; each reminder sets
  `In-Reply-To`/`References` (+ identical subject) so Gmail threads them. IDs derive from koid + a seq counter.
- Best-effort step failures (`_runner`) post to the **Kickstart Logs** channel via `notify.send_log`.

## Development

```bash
./run-local.sh              # Flask on :5002 (DTADMIN_ENV=dev). Needs docker infra: garage-db :5544/crm + ElasticMQ :9324.
```
- Env/config comes from `axle/.env` (not committed). Local uses MOCK tenant-create (LD) + Maglev Slack;
  Drive (`:8080`) must be up for `cleanup_demo_users` or register dead-letters.
- **No test framework** in this repo. Verify with `python -m py_compile <files>` + local `curl` against
  `:5002` (public `POST /intake/<token>` needs no auth; drivetrainer routes need an Okta bearer).

## Gotchas

1. **Grouped vs flat** — stored data is grouped; read flat via `flatten_groups` / `KickoffData`. A field
   like `time_zone`/`connectors` lives inside `company_details`/`deal_details`, not top-level.
2. **JSONB writes** — reassign the dict; never mutate in place (dirty-check misses it → silent drop).
3. **references = merge, not overwrite** — `merge_references` preserves earlier keys (e.g. `deck_linear_id`
   survives a register-redelivery's smaller refs return).
4. **Best-effort** — never let a Linear/Slack/Monday failure raise out of a step and strand the kickoff;
   `record_task_error` for observability; only retry idempotent work.
5. **recipients** — never hardcode a team/assignee/channel; add it to `Recipients` (both PROD + NONPROD).
6. **Sub-issue identity** — a referenced sub-issue (deck) is keyed by a title CONSTANT used for both the
   tuple title and the match, so a rename stays in sync.
7. **Stringer secret** — axle can read the shared **drive-secrets** bundle, so `STRINGER_API_KEY`
   (`settings.stringer_api_key`) is sourced from there — the same value Drive reads as its `stringer.api-key`
   property, which must equal f_stringer's `SELF_API_KEY`. Stringer is single-env (prod), reached over its
   in-cluster Service (`http://stringer-svc.garage.svc.cluster.local`); local dev hits `:9042`.
