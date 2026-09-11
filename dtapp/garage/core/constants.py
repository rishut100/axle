# Stable, non-secret Kickstart values — code constants, not env (mirrors Drive's Constants).
# Env-resolved "who/where" routing (Linear teams + assignees, assign-DM, email CC) lives in
# core/recipients.py, not here.

# Okta token audience — the default authorization server's audience; stable across envs (only the
# ISSUER varies per env, so that stays config). Mirrors Heimdall's hardcoded OktaAuth.AUDIENCE.
OKTA_AUDIENCE = "api://default"

# Okta issuer — the product SPA's Okta; single const across envs (FE + BE both validate against it).
OKTA_ISSUER = "https://auth.example-corp.okta.com/oauth2/default"

# LD env-flag (service context) gating the post-provisioning tenant-verify feature. Off locally.
VERIFY_TENANT_FLAG = "kickstart-verify-tenant"
MOCK_PROVISIONING_FLAG = "kickstart-mock-provisioning"  # LD: mock create_tenant (prod-testing)
KICKSTART_DELETE_FLAG = "kickstart-delete"  # LD: gates deleting a real kickoff (FE + BE); ops-lead-only
# Verify cadence: 3 health checks, +5/+10/+15 min after provisioning completes.
VERIFY_MAX_ATTEMPTS = 3
VERIFY_DELAY_SECONDS = 300

# verify-tenant / dead-letter escalation targets — prod SRE routing (fire only in prod).
ASK_SRE_SLACK_CHANNEL_ID = "C00000000"     # #ask-sre
SRE_SUBTEAM_ID = "S00000000"               # @sre-team usergroup (<!subteam^…> mention)
SENDGRID_FROM_EMAIL = "team@example.com"
SENDGRID_FROM_NAME = "Product Team"

# Axle's own provisioning SQS queue — same name every env (ElasticMQ locally, real SQS in prod).
KICKSTART_PROVISIONING_QUEUE_NAME = "kickstart-provisioning"

# S3 bucket for garage asset uploads — env-independent (same bucket every env; MinIO locally, real S3 in prod).
GARAGE_S3_BUCKET = "dt-garage"

LINEAR_PRIORITY_URGENT = 1

# Academy-access-request body (MARKT-2843): column defaults + the cc-mention markup (pings on
# create). The table is assembled from _ACADEMY_COLUMNS in completion._academy_desc.
LINEAR_ACADEMY_MANAGER = "Academy Manager"   # "Academy Manager (Onboarding/Tenant PoC)" column default
LINEAR_ACADEMY_COURSE = "V3"       # "Course to assign" column default
LINEAR_ACADEMY_CC = 'cc <user id="00000000-0000-0000-0000-000000000000">academy-owner</user>'

# ── Monday template board (created inside each tenant's workspace) ──
MONDAY_BOARD_TEMPLATE_ID = "00000000"          # template_center template — same across all envs
MONDAY_BOARD_TEMPLATE_NAME = "Cobuild Handoff Tracker"  # create_board uses this as the board_name
MONDAY_BOARD_KIND = "public"                    # BoardKind — workspace members can see it
MONDAY_ACCOUNT_SLUG = "your-account"            # <slug>.monday.com — for the workspace deep-link
MONDAY_WORKSPACE_KIND = "open"                   # Monday WorkspaceKind: "open" (account-wide) | "closed" (subscribers only)

# ── Fixed product references (same across all envs — not env-resolved) ──
SLACK_WORKSPACE_URL = "https://your-workspace.slack.com"  # workspace base for channel deep-links
TENANT_SITE_DOMAIN = "example.com"              # customer Drive tenant site: <subdomain>.<domain>
CONSULTANT_SHEET_ID = "1XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"  # consultant roster Google Sheet
CONSULTANT_SHEET_TAB = "All"                     # roster sheet tab

# ── Kickstart reminders (Phase 3) ── cron timings + tunables (configurable; NOT env/secret).
# Cron expressions are evaluated by dtapp/cron_scheduler.py (croniter). Prod runs TZ=Asia/Kolkata → IST.
INTAKE_REMINDER_CRON = "0 14 * * *"        # daily 2:00 PM IST — nudge the AE while intake is unfilled
CONSULTANT_REMINDER_CRON = "5 14 * * *"    # daily 2:05 PM IST — nudge the ops lead to assign a consultant
CONSULTANT_REMINDER_LEAD_DAYS = 4          # start reminding from (kickoff_date - LEAD_DAYS)
INTAKE_REMINDER_START_LAG_DAYS = 1         # skip the registration day; remind from the next day onward
REMINDER_BUSINESS_DAYS_ONLY = False        # True → skip Sat/Sun
# scheduler.add_job ids (stable dedup keys on the shared CronScheduler)
INTAKE_REMINDER_JOB_ID = "kickstart_intake_reminder"
CONSULTANT_REMINDER_JOB_ID = "kickstart_consultant_reminder"
