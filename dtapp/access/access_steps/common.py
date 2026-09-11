"""Per-tool dispatch registry (decision #5). Keyed by ToolMatrixEntry.client_name — at ~50 tools an
if/elif chain per step doesn't scale the way it does for kickstart's 5 hardcoded vendors, so grant.py/
revoke.py iterate the checklist and look the tool's client up here instead of branching on tool_name
inline. Add a new tool by adding a matrix row + (if it's not plain SCIM) one client file + one registry
entry — never by editing grant.py/revoke.py.
"""
from dtapp.access.clients import make_scim_client, github_client
from dtapp.access.clients.okta_client import okta_corp_client
from dtapp.access.clients.datadog_client import datadog_client
from dtapp.access.clients.posthog_client import posthog_client
from dtapp.access.clients.hubspot_client import hubspot_client
from dtapp.access.clients.gitbook_client import gitbook_client
from dtapp.access.clients.instantly_client import instantly_client
from dtapp.access.clients.clockify_client import clockify_client
from dtapp.access.clients.coderabbit_client import coderabbit_client
from dtapp.access.clients.temporal_client import temporal_client
from dtapp.access.clients.microsoft365_client import microsoft365_client
from dtapp.access.clients.zoom_client import zoom_client
from dtapp.access.clients.gworkspace_client import gworkspace_client
from dtapp.access.clients.gcp_iam_client import gcp_iam_client
from dtapp.access.clients.aws_iam_client import aws_iam_client
from dtapp.access.clients.monday_client import monday_adapter
from dtapp.access.clients.slack_channel_client import slack_channel_client
from dtapp.access.clients.linear_org_client import linear_org_client
from dtapp.access.clients.keka_client import keka_client
from dtapp.access.clients.plum_client import plum_client

_REGISTRY = {
    "github": github_client,
    "okta": okta_corp_client,
    "datadog": datadog_client,
    "posthog": posthog_client,
    "hubspot": hubspot_client,
    "gitbook": gitbook_client,
    "instantly": instantly_client,
    "clockify": clockify_client,
    "coderabbit": coderabbit_client,
    "temporal": temporal_client,
    "microsoft365": microsoft365_client,
    "zoom": zoom_client,
    "gworkspace": gworkspace_client,
    "gcp_iam": gcp_iam_client,
    "aws_iam": aws_iam_client,
    "monday": monday_adapter,
    "slack_channel": slack_channel_client,
    "linear_org": linear_org_client,
    "keka": keka_client,
    "plum": plum_client,
}


def get_client(client_name: str, client_config: dict):
    """Resolve a matrix row's client_name to a ready client instance. `scim` is the generic factory
    (config-driven per row); anything else is a bespoke singleton looked up in _REGISTRY."""
    if client_name == "scim":
        return make_scim_client(client_config.get("tool_name", "unknown"), client_config)
    if client_name in _REGISTRY:
        return _REGISTRY[client_name]
    raise KeyError(f"no client registered for client_name={client_name!r}")
