from dtapp.access.clients.scim_client import make_scim_client
from dtapp.access.clients.github_client import github_client
# Reused directly from kickstart — same shape (ServiceClient wrap, dry-run-when-unconfigured), no
# access-specific behavior needed. NOTE: garage/kickstart's slack_client is a SEPARATE Slack app/token
# from the one this module uses for owner notifications (dtapp.services.slack_service) — see
# access_steps/grant.py's import comment for why. Not re-exported here to avoid the two being confused.
from dtapp.garage.kickstart.clients import linear_client, monday_client

__all__ = ["make_scim_client", "github_client", "linear_client", "monday_client"]
