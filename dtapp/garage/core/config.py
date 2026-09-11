# Kickstart configuration — reads from ENV VARS (12-factor), not app.config.
# Diverges from Axle's env/dev.py→app.config pattern intentionally so the
# kickstart module is framework-agnostic. Provide KICKSTART_* / OKTA_* as environment
# variables (or in a .env file at the repo root).
from enum import Enum
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Deployment env (axle's DTADMIN_ENV). Mirrors Heimdall's Environment pattern."""
    DEV = "dev"
    STAGING = "staging"
    PREPROD = "preprod"
    PROD = "prod"


class KickstartSettings(BaseSettings):
    # App-wide deployment env (axle's DTADMIN_ENV). Default prod = fail-closed (Heimdall convention):
    # an unset/unknown value behaves as prod (real recipients), never silently as a non-prod env.
    dtadmin_env: str = "prod"

    garage_db_host: str = "localhost"
    garage_db_name: str = "axle"
    garage_db_user: str = "postgres"
    garage_db_port: int = 5432
    garage_db_pass: str = ""

    # Phase-2: Axle's OWN single SQS queue (producer + consumer both Axle); body = {koid}.
    # ElasticMQ locally; real SQS in prod. Drive runs no SQS. Queue NAME is a constant
    # (constants.KICKSTART_PROVISIONING_QUEUE_NAME); only the endpoint varies per env.
    garage_sqs_endpoint_url: Optional[str] = None

    # LaunchDarkly server SDK key — drives server-side access gating (assign/debug) + the
    # verify-tenant feature flag (Heimdall pattern). Blank locally → LD not enforced (permissive);
    # set in prod → flags enforced (fail-closed on oncall). See core/flags.py.
    launchdarkly_sdk_key: str = ""

    aws_region: str = "us-east-1"

    # SendGrid — API key only (sender + base/layout template id are constants, mirror Drive's Constants)
    sendgrid_api_key: str = ""

    # Drive internal API secret (base URL is a const in kickstart/clients/drive_client.py).
    axle_api_key: str = ""

    # Slack — Kickstart Agent bot token (secret). The announce channel is env-resolved in
    # core/recipients.py (not config).
    kickstart_slack_bot_token: str = ""

    # Beta/env routing OVERRIDES (so a beta deploy needs no temp code branch): when set, override the
    # env-resolved recipients (core/recipients.py) — route the GTM "new signing" announce + the
    # "assign a consultant" notice to a specific channel. Blank → use the env defaults.
    # KICKSTART_ASSIGN_NOTIFY may be a channel id (C…/G… → posted to it) or a user id (→ DM) — see
    # slack_client.send_dm.
    garage_announce_channel: str = ""
    garage_assign_notify: str = ""

    # Linear — API key only. Teams/assignees are env-resolved in core/recipients.py (not config).
    linear_api_key: str = ""

    # Monday.com — API key + per-tenant workspace settings. At provisioning a workspace is created
    # per kickoff ("T<tenantId> <Company>"); the base team + assignees are added as owners.
    monday_api_key: str = ""

    # f_stringer internal-API key (x-api-key == f_stringer's SELF_API_KEY).
    stringer_api_key: str = ""

    # Google Sheets — full service-account JSON string (mirrors GOOGLE_SERVICE_ACCOUNT_JSON in Heimdall)
    google_service_account_json: str = ""

    # S3 (MinIO local via GARAGE_S3_ENDPOINT_URL; blank → real AWS S3). Creds blank → boto3 default chain (prod IAM).
    # Bucket is a code constant (constants.GARAGE_S3_BUCKET) — env-independent.
    garage_s3_endpoint_url: Optional[str] = None
    garage_s3_access_key: str = ""
    garage_s3_secret_key: str = ""

    # ── Access automation (dtapp/access) — ENG-90754 ──
    # Shared-secret header check on the Keka webhook (no HMAC signing on Keka's side to verify against,
    # unlike Slack). Blank in non-prod → the webhook route accepts unauthenticated calls (local testing
    # only); blank in prod → the route 500s (fail-closed, never silently accept unverified webhooks).
    keka_webhook_secret: str = ""
    # Keka's own API (used to pull richer employee fields the webhook payload doesn't carry, and as the
    # source for a future poll-based reconciliation). Blank → any Keka API lookups dry-run.
    keka_api_key: str = ""
    keka_api_base_url: str = "https://api.keka.com"
    # Keka HRIS as an access-module TARGET (creating/exiting the employee's own Keka login) — OAuth2,
    # not the simple api_key above. Reclassified from manual after re-checking: Keka's REST API does
    # support employee create + exit-request management (see access/clients/keka_client.py).
    keka_oauth_client_id: str = ""
    keka_oauth_client_secret: str = ""

    # Plum (health insurance) — Partner API. Reclassified from manual after re-checking: Plum's
    # Partner API genuinely supports add/remove member + dependents (partners.plumhq.com).
    plum_partner_api_key: str = ""
    plum_company_id: str = ""

    # GitHub — org-scoped PAT/App token (bespoke client; most other tools go through the generic SCIM
    # client instead, configured per-tool in the access_tool_matrix.client_config JSONB column rather
    # than as individual settings fields here — see access/clients/scim_client.py). Blank → dry-run.
    github_api_token: str = ""
    github_org: str = "DrivetrainAi"

    # Corporate Okta (drivetrain.okta.com) — DISTINCT from the customer-facing product Okta
    # (constants.DRIVETRAIN_OKTA / OKTA_API_TOKEN used by views/okta.py for per-tenant customer SSO).
    # "Drivetrain" access for an employee (per this team's own convention — "Okta and Drivetrain are
    # the same") means corporate Okta group membership, granted via the Users/Groups API. Blank → dry-run.
    okta_corp_api_token: str = ""
    okta_corp_base_url: str = "https://drivetrain.okta.com"
    okta_corp_drivetrain_group_id: str = ""  # the Okta group whose membership = "has Drivetrain access"

    # Bespoke clients (dtapp/access/clients/) — one settings block per tool below. Every field blank →
    # that tool's client dry-runs (settings.dryrun_unconfigured), same convention as GitHub/Okta above.
    # Datadog — bespoke, NOT generic SCIM (real API needs DD-API-KEY + DD-APPLICATION-KEY together,
    # not a bearer token). Confirmed live 2026-09-07: a metrics-scoped key returns 403 on every
    # user-management call — needs a key pair from an account with User Access Manage/Admin permission.
    datadog_api_key: str = ""
    datadog_app_key: str = ""

    posthog_api_key: str = ""
    posthog_org_id: str = ""
    posthog_base_url: str = "https://app.posthog.com"

    hubspot_api_token: str = ""
    hubspot_default_role_id: str = ""

    gitbook_api_token: str = ""
    gitbook_org_id: str = ""

    instantly_api_key: str = ""

    clockify_api_key: str = ""
    clockify_workspace_id: str = ""

    coderabbit_api_key: str = ""

    temporal_cloud_api_key: str = ""

    # Monday.com — reuses kickstart's existing monday_api_key (see kickstart section below).
    # monday_home_workspace_id: the one workspace new hires get added to as owners — Monday's public
    # API has no "invite brand-new email to the whole account" endpoint, only add-existing-user-to-
    # workspace/board; see access/clients/monday_client.py for the honest limitation.
    monday_home_workspace_id: str = ""

    # Microsoft 365 / Entra ID (Graph API) — OAuth2 client-credentials flow, not a static bearer token.
    ms_graph_tenant_id: str = ""
    ms_graph_client_id: str = ""
    ms_graph_client_secret: str = ""

    # Zoom — OAuth2 Server-to-Server (JWT apps were deprecated), also client-credentials-style.
    zoom_account_id: str = ""
    zoom_client_id: str = ""
    zoom_client_secret: str = ""

    # Google Workspace (G-suite) Admin SDK — reuses the SAME service-account JSON as
    # google_service_account_json (main.py's BigQuery credential) via domain-wide delegation, PLUS a
    # super-admin email to impersonate (domain-wide delegation requires a "subject" to act as).
    gworkspace_admin_impersonate_email: str = ""

    # AWS IAM Identity Center — auth reuses this machine's/pod's existing AWS credential chain (boto3
    # default chain — the same one main.py's boto3 clients already use), NOT a separate token setting.
    aws_identity_store_id: str = ""
    aws_identity_center_instance_arn: str = ""
    aws_target_account_id: str = ""       # the AWS account new hires get assigned into
    aws_permission_set_arn: str = ""      # the permission set granted on that account

    # GCP IAM — engineers only ever get GCP access on staging + preprod, never prod. ONE credential
    # covers everything (grant on staging+preprod, AND the revoke-side dynamic sweep across every
    # project it can see) — consolidated 2026-09-07 after finding drive-backend@composed-strata-301915
    # already has real, standing access to both staging/preprod plus ~40 other projects (no org-level
    # grant needed; whoever set this SA up already added it project-by-project). Distinct from
    # google_service_account_json (BigQuery/Workspace, a different, narrower-scoped identity).
    gcp_iam_service_account_json: str = ""
    gcp_project_id: str = ""              # drivetrain-staging
    gcp_preprod_project_id: str = ""      # drivetrain-preprod
    gcp_default_role: str = "roles/viewer"

    # Access-automation Slack channel overrides (PROD only — see access/recipients.py; non-prod always
    # routes to the existing Shahbaz dev-test channel regardless of these). Blank in prod → that
    # channel's posts are skipped + logged, not a hard failure (a notify miss must not crash the queue
    # consumer), but MUST be set before this module is relied on in prod.
    access_notify_channel: str = ""
    access_logs_channel: str = ""
    access_drift_alert_channel: str = ""
    # Manual-tool sub-issue Linear routing override (PROD). Blank → falls back to the Eng Dev Testing
    # bucket even in prod, which is a misconfiguration (tickets should go to a real team) — logged loudly
    # at recipients.py import time.
    access_default_linear_team: str = ""
    access_default_linear_state: str = ""

    @property
    def garage_db_url(self) -> str:
        return f"postgresql+psycopg2://{self.garage_db_user}:{self.garage_db_pass}@{self.garage_db_host}:{self.garage_db_port}/{self.garage_db_name}"

    @property
    def environment(self) -> Environment:
        """Parse DTADMIN_ENV into the enum; unknown/unset → PROD (fail-closed, Heimdall convention)."""
        try:
            return Environment((self.dtadmin_env or "").strip().lower())
        except ValueError:
            return Environment.PROD

    @property
    def is_prod(self) -> bool:
        """True only in the prod deployment. Every non-prod env routes kickoff side-effects to
        Shahbaz + the Eng Dev Testing Linear bucket (see core/recipients.py)."""
        return self.environment == Environment.PROD

    @property
    def dryrun_unconfigured(self) -> bool:
        """Env-derived (not a manual flag): in non-prod, a step whose service key is blank LOGs its
        payload and continues (dry-run) instead of failing loud. PROD is ALWAYS False — prod must be
        fully configured / fail loud, never silently simulate."""
        return not self.is_prod

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )


settings = KickstartSettings()
