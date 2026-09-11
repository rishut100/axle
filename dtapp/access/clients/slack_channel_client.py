"""The 'gpt-support-<person>' row isn't really a third-party admin API case — per #tool-access-poc
history it's just "create a Slack channel named gpt-support-<firstname>". Wraps kickstart's existing
slack_client.create_channel (the garage/kickstart Slack app, since this is a channel-creation action,
not the top-level bot's owner-notification job)."""
import logging

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError
from dtapp.garage.kickstart.clients.slack_client import slack_client as _kickstart_slack

logger = logging.getLogger(__name__)


class SlackChannelClient:
    def _configured(self) -> bool:
        return bool(settings.kickstart_slack_bot_token)

    def invite_member(self, email: str) -> dict:
        """Creates (or reuses, if already taken) 'gpt-support-<firstname>'."""
        first_name = email.split("@")[0].split(".")[0].lower()
        channel_name = f"gpt-support-{first_name}"
        channel_id = _kickstart_slack.create_channel(channel_name)
        return {"id": channel_id, "channel_name": channel_name}

    def remove_member(self, email: str) -> None:
        """No teardown defined — a support channel is typically archived manually by whoever ran the
        support engagement, not auto-deleted on offboarding. Intentional no-op, not a gap."""
        return

    def get_user_status(self, email: str) -> str:
        first_name = email.split("@")[0].split(".")[0].lower()
        channel_id = _kickstart_slack.find_channel_by_name(f"gpt-support-{first_name}")
        return "active" if channel_id else "removed"


slack_channel_client = SlackChannelClient()
