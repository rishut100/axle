"""Env-resolved routing + base URLs for kickoff side-effects — the Linear teams + assignees, the
assign-DM recipient, the requisition-email CC, and the Garage FE base URL (intake form + kickoff
deep-links). PROD targets the real people/buckets; every NON-prod env (dev/staging/preprod) routes to
Shahbaz + the 'Eng Dev Testing' Linear bucket, so staging/dev never ping real folks. Single switch
point: `recipients = _PROD if settings.is_prod else _NONPROD`."""
from dataclasses import dataclass

from dtapp.garage.core.config import settings


@dataclass(frozen=True)
class Recipients:
    linear_team: str          # Linear team for the kickoff parent + all kickoff sub-issues + academy
    connectors_team: str      # Linear team for the per-connector issues
    connector_state_id: str   # per-connector issues open in this state ("Todo now")
    kickoff_state_id: str     # Kickoff-team "Todo" — every kickoff sub-issue + academy issue opens here
    linear_jason: str         # most kickoff sub-issues assignee (Jason in prod)
    linear_fauzan: str        # support-bot + master-sheet sub-issues (Fauzan in prod)
    linear_praneeth: str      # per-connector assignee (Praneeth in prod)
    linear_ankit: str         # "verify external channel on Heimdall" assignee (Ankit G in prod)
    linear_wayne: str         # academy-access-request assignee (Wayne in prod)
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


# Shahbaz — the catch-all identity for every non-prod env.
_SHAHBAZ_LINEAR = "7458f8e6-96e7-4006-81a1-9b3a8c8319f0"
_SHAHBAZ_SLACK = "U09LWQNG0S0"
_ENG_DEV_TESTING_TEAM = "2ef1b4b0-6c41-4f0e-9832-66b70028d3fe"  # non-prod Linear bucket "Eng Dev Testing"

_PROD = Recipients(
    linear_team="3dba2e06-7e4c-4178-ab7f-eae8b27beee8",         # team "Kickoff" (KIC-)
    connectors_team="ae89b650-dae4-4d26-ac52-d32751454be0",     # team "Connectors" (CON2-)
    connector_state_id="d8cb1581-49b2-4609-afd6-8e8cba417d4b",  # CON2 "Todo now"
    kickoff_state_id="b7728b8a-65e1-4fa8-89e9-010e79018818",    # Kickoff team "Todo"
    linear_jason="9050388b-a439-4194-a841-bb55187f438b",
    linear_fauzan="3838b29f-f7c2-4206-aff8-8261d6b0f4e3",   # Fauzan Khan (fauzan@drivetrain.ai)
    linear_praneeth="320680ba-584c-4f36-aa8f-ff4c324a7253",
    linear_ankit="1321a50f-0fc4-4405-b721-cefe2d322842",    # Ankit G (ankit.g@drivetrain.ai)
    linear_wayne="e5d2e6b1-bfe3-42ad-af6f-495699185c9f",    # Wayne (wayne@drivetrain.ai)
    marketing_team="9e05273c-f919-466f-878e-7c3fd6d4fc97",       # team "Marketing" (MARKT-)
    marketing_state_id="b3498283-239f-462a-bb8d-44c577cd13c3",   # Marketing team "Todo"
    paaras_slack_id="U038LS7JV1P",  # Paaras Sharma (paaras@drivetrain.ai)
    announce_channel="C07ER1S9Z8Q",  # #gtm-process-commercial-kickoff (private)
    # Paaras, Mona, Jason, Fauzan — @-mentioned in the GTM "new signing" post.
    gtm_notify_slack_ids=("U038LS7JV1P", "U077A936YTV", "U07UN8HS05T", "U09G8H2B1R7"),
    requisition_cc=("onboarding@drivetrain.ai",),  # + the AE (registered_by), CC'd dynamically
    # Standing members auto-added to every internal Slack channel: Alok, Tark, Saurav, Paaras, Jason,
    # Fauzan, Praneeth, Chitranshu, Chandan (+ the AE, added dynamically).
    slack_channel_members=("U01NCQ1Q2AC", "U01HFHS7NKF", "U01P7LH8DS5", "U038LS7JV1P", "U07UN8HS05T",
                           "U09G8H2B1R7", "U086W1BQJD7", "U0BBD3090G0", "U07UPC4CHBL"),
    monday_workspace_owners=("paaras@drivetrain.ai", "jason@drivetrain.ai", "ankit.g@drivetrain.ai"),
    form_base_url="https://garage.drivetrain.ai/app",
    notify_channel="C0BS2NBQDK7",   # #kickstart-notifications
    logs_channel="C0BS2NMJ1S9",     # #kickstart-logs
)
_NONPROD = Recipients(
    linear_team=_ENG_DEV_TESTING_TEAM,
    connectors_team=_ENG_DEV_TESTING_TEAM,
    connector_state_id="598a4647-2a56-40f2-a7f4-a493c17b5748",  # Eng Dev Testing "Todo"
    kickoff_state_id="598a4647-2a56-40f2-a7f4-a493c17b5748",    # Eng Dev Testing "Todo"
    linear_jason=_SHAHBAZ_LINEAR,
    linear_fauzan=_SHAHBAZ_LINEAR,
    linear_praneeth=_SHAHBAZ_LINEAR,
    linear_ankit=_SHAHBAZ_LINEAR,
    linear_wayne=_SHAHBAZ_LINEAR,
    marketing_team=_ENG_DEV_TESTING_TEAM,
    marketing_state_id="598a4647-2a56-40f2-a7f4-a493c17b5748",  # Eng Dev Testing "Todo"
    paaras_slack_id=_SHAHBAZ_SLACK,
    announce_channel="C0B99N8AVFZ",  # #shahbaz-dev-test
    gtm_notify_slack_ids=(_SHAHBAZ_SLACK,),
    requisition_cc=("shahbaz@drivetrain.ai",),
    slack_channel_members=(_SHAHBAZ_SLACK,),  # non-prod: only Shahbaz — never ping real folks
    monday_workspace_owners=("shahbaz@drivetrain.ai",),
    form_base_url="http://localhost:5173",
    notify_channel="C0B99N8AVFZ",   # #shahbaz-dev-test (Shahbaz Dev)
    logs_channel="C0B99N8AVFZ",     # #shahbaz-dev-test (Shahbaz Dev)
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
