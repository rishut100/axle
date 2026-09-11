"""Env-resolved routing + base URLs for kickoff side-effects — the Linear teams + assignees, the
assign-DM recipient, the requisition-email CC, and the Garage FE base URL (intake form + kickoff
deep-links). PROD targets the real people/buckets; every NON-prod env (dev/staging/preprod) routes to
a fixed dev-safety-net identity + the 'Eng Dev Testing' Linear bucket, so staging/dev never ping real
folks. Single switch point: `recipients = _PROD if settings.is_prod else _NONPROD`."""
from dataclasses import dataclass

from dtapp.garage.core.config import settings


@dataclass(frozen=True)
class Recipients:
    linear_team: str          # Linear team for the kickoff parent + all kickoff sub-issues + academy
    connectors_team: str      # Linear team for the per-connector issues
    connector_state_id: str   # per-connector issues open in this state ("Todo now")
    kickoff_state_id: str     # Kickoff-team "Todo" — every kickoff sub-issue + academy issue opens here
    linear_jason: str         # most kickoff sub-issues assignee (name redacted for public repo)
    linear_fauzan: str        # support-bot + master-sheet sub-issues (name redacted for public repo)
    linear_praneeth: str      # per-connector assignee (name redacted for public repo)
    linear_ankit: str         # "verify external channel on Heimdall" assignee (name redacted for public repo)
    linear_wayne: str         # academy-access-request assignee (name redacted for public repo)
    marketing_team: str       # Linear team for the Academy access request (Marketing / MARKT- in prod)
    marketing_state_id: str   # Marketing-team "Todo" — the Academy access request opens here
    paaras_slack_id: str      # "assign a consultant" DM recipient
    announce_channel: str     # Stage-A "new kickoff" announce channel
    gtm_notify_slack_ids: tuple  # @-mentioned in the GTM "new signing" post (Slack user ids)
    requisition_cc: tuple     # standing intake-email CC list (the AE / registered_by is added dynamically)
    slack_channel_members: tuple  # standing members auto-invited to every internal Slack channel (user ids)
    monday_workspace_owners: tuple  # base owners added to every per-tenant Monday workspace (emails)
    form_base_url: str              # Garage FE base URL — intake form (/intake/<token>) + kickoff deep-links (/kickstart/<koid>)
    notify_channel: str             # central "Kickstart Notifications" channel (intake reminders + future alerts)
    logs_channel: str               # central "Kickstart Logs" channel (step-failure alerts)


# The fixed dev-safety-net identity — catch-all for every non-prod env.
# Real Linear/Slack ids + employee email addresses below are placeholders for the public repo — replace with your own.
_SHAHBAZ_LINEAR = "00000000-0000-0000-0000-000000000001"
_SHAHBAZ_SLACK = "U00000001"
_ENG_DEV_TESTING_TEAM = "00000000-0000-0000-0000-000000000002"  # non-prod Linear bucket "Eng Dev Testing"

_PROD = Recipients(
    linear_team="00000000-0000-0000-0000-000000000003",         # team "Kickoff" (KIC-)
    connectors_team="00000000-0000-0000-0000-000000000004",     # team "Connectors" (CON2-)
    connector_state_id="00000000-0000-0000-0000-000000000005",  # CON2 "Todo now"
    kickoff_state_id="00000000-0000-0000-0000-000000000006",    # Kickoff team "Todo"
    linear_jason="00000000-0000-0000-0000-000000000007",
    linear_fauzan="00000000-0000-0000-0000-000000000008",   # name redacted for public repo
    linear_praneeth="00000000-0000-0000-0000-000000000009",
    linear_ankit="00000000-0000-0000-0000-00000000000a",    # name redacted for public repo
    linear_wayne="00000000-0000-0000-0000-00000000000b",    # name redacted for public repo
    marketing_team="00000000-0000-0000-0000-00000000000c",       # team "Marketing" (MARKT-)
    marketing_state_id="00000000-0000-0000-0000-00000000000d",   # Marketing team "Todo"
    paaras_slack_id="U00000002",  # name redacted for public repo
    announce_channel="C00000001",  # #gtm-process-commercial-kickoff (private)
    # Names redacted for the public repo — @-mentioned in the GTM "new signing" post.
    gtm_notify_slack_ids=("U00000002", "U00000003", "U00000004", "U00000005"),
    requisition_cc=("onboarding-team@example.com",),  # + the AE (registered_by), CC'd dynamically
    # Standing members auto-added to every internal Slack channel (names redacted for public repo)
    # + the AE, added dynamically.
    slack_channel_members=("U00000006", "U00000007", "U00000008", "U00000002", "U00000004",
                           "U00000005", "U00000009", "U0000000a", "U0000000b"),
    monday_workspace_owners=("owner1-team@example.com", "owner2-team@example.com", "owner3-team@example.com"),
    form_base_url="https://garage.example.com/app",
    notify_channel="C00000002",   # #kickstart-notifications
    logs_channel="C00000003",     # #kickstart-logs
)
_NONPROD = Recipients(
    linear_team=_ENG_DEV_TESTING_TEAM,
    connectors_team=_ENG_DEV_TESTING_TEAM,
    connector_state_id="00000000-0000-0000-0000-00000000000e",  # Eng Dev Testing "Todo"
    kickoff_state_id="00000000-0000-0000-0000-00000000000e",    # Eng Dev Testing "Todo"
    linear_jason=_SHAHBAZ_LINEAR,
    linear_fauzan=_SHAHBAZ_LINEAR,
    linear_praneeth=_SHAHBAZ_LINEAR,
    linear_ankit=_SHAHBAZ_LINEAR,
    linear_wayne=_SHAHBAZ_LINEAR,
    marketing_team=_ENG_DEV_TESTING_TEAM,
    marketing_state_id="00000000-0000-0000-0000-00000000000e",  # Eng Dev Testing "Todo"
    paaras_slack_id=_SHAHBAZ_SLACK,
    announce_channel="C00000004",  # #dev-test (non-prod catch-all channel)
    gtm_notify_slack_ids=(_SHAHBAZ_SLACK,),
    requisition_cc=("dev-team@example.com",),
    slack_channel_members=(_SHAHBAZ_SLACK,),  # non-prod: only the dev safety-net identity — never ping real folks
    monday_workspace_owners=("dev-team@example.com",),
    form_base_url="http://localhost:5173",
    notify_channel="C00000004",   # #dev-test (non-prod catch-all channel)
    logs_channel="C00000004",     # #dev-test (non-prod catch-all channel)
)

recipients = _PROD if settings.is_prod else _NONPROD

# Env routing overrides (retire the temp beta-jugaad branch): a deploy can point the GTM announce +
# the "assign a consultant" notice at a specific channel via env, no code branch. Blank → keep defaults.
# KICKSTART_ASSIGN_NOTIFY may be a channel id (C…/G…) or a user id — slack_client.send_dm routes both.
import dataclasses as _dataclasses  # noqa: E402

_overrides = {}
if settings.garage_announce_channel:
    _overrides["announce_channel"] = settings.garage_announce_channel
if settings.garage_assign_notify:
    _overrides["paaras_slack_id"] = settings.garage_assign_notify
if _overrides:
    recipients = _dataclasses.replace(recipients, **_overrides)
