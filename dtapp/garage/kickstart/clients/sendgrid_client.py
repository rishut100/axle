import html
import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Cc, From, Header, Mail

from dtapp.garage.core import constants
from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError
from dtapp.garage.kickstart.recipients import recipients
# token minting happens in the caller (register txn); this client only sends.

logger = logging.getLogger(__name__)

# Shared SG base/layout template (branded shell; subject/body injected). Constant, not env — mirrors Drive's Constants.BASIC_EMAIL_TEMPLATE_ID.
BASE_EMAIL_TEMPLATE_ID = "d-REPLACE_WITH_TEMPLATE_ID"
# Identical subject on the invite + every reminder so Gmail threads them (with the Message-ID headers).
_SUBJECT = "Set up your Workspace"
_SEND_TIMEOUT = 15  # seconds — bound the send (urllib default = none) so a hung call can't stall the thread

# Deterministic Message-IDs so a reminder's References/In-Reply-To point at the invite's id (Gmail threads them).
_MSGID_DOMAIN = "example.com"


def intake_msgid(koid) -> str:
    return f"<kickstart-{koid}-intake@{_MSGID_DOMAIN}>"


def reminder_msgid(koid, n: int) -> str:
    return f"<kickstart-{koid}-reminder-{n}@{_MSGID_DOMAIN}>"


_FF = "font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;"


def _step_row(n, title, rest):
    return f"""<tr>
      <td valign="top" width="34" style="padding:0 14px 16px 0;">
        <div style="width:26px;height:26px;border-radius:13px;background:#0f172a;color:#ffffff;{_FF}font-size:13px;font-weight:700;line-height:26px;text-align:center;">{n}</div>
      </td>
      <td valign="top" style="padding:1px 0 16px;{_FF}font-size:15px;line-height:1.5;color:#334155;"><strong style="color:#0f172a;">{title}</strong> {rest}</td>
    </tr>"""


def _cta_button(link):
    """Branded 'Set up my workspace' CTA button — shared by the invite + reminder bodies."""
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto 12px;">
  <tr>
    <td align="center" bgcolor="#5c45ff" style="border-radius:10px;">
      <a href="{link}" target="_blank" rel="noopener" style="display:inline-block;padding:16px 38px;{_FF}font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:10px;background:#5c45ff;box-shadow:0 2px 8px rgba(92,69,255,0.35);">Set up my workspace &rarr;</a>
    </td>
  </tr>
</table>"""


def _fallback_link(link):
    """'Button not working? paste this link' fallback block — shared by the invite + reminder bodies."""
    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0;">
  <tr>
    <td style="padding:16px 18px;background:#f8fafc;border-radius:8px;{_FF}font-size:13px;line-height:1.5;color:#94a3b8;">
      Button not working? Paste this into your browser:<br>
      <a href="{link}" style="color:#5c45ff;word-break:break-all;">{link}</a>
    </td>
  </tr>
</table>"""


def _intake_body_html(company_name, intake_link):
    """Content fragment injected into the shared SG base template's {{{body}}} — the branded shell (header/footer) comes from the template."""
    name = html.escape((company_name or "there").strip())
    link = html.escape(intake_link, quote=True)
    steps = (
        _step_row(1, "Share a few details", "about your domain, fiscal year, and who to invite.")
        + _step_row(2, "We set up your workspace", "and provision it, bootstrapping everything.")
        + _step_row(3, "We email you to sign in", "and you&rsquo;re ready to build.")
    )
    return f"""\
<p style="margin:0 0 8px;{_FF}font-size:23px;line-height:1.3;font-weight:700;color:#0f172a;">Welcome aboard, {name} 👋</p>
<p style="margin:0 0 30px;{_FF}font-size:16px;line-height:1.65;color:#475569;">You&rsquo;re almost set. Share a few quick details below and we&rsquo;ll provision your Drivetrain workspace. When it&rsquo;s ready, you&rsquo;ll get an invite email to sign in and a confirmation that your workspace is live.</p>

<p style="margin:0 0 14px;{_FF}font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8;">What happens next</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 32px;">{steps}</table>

{_cta_button(link)}
<p style="margin:0 0 30px;text-align:center;{_FF}font-size:13px;color:#94a3b8;">Secure, single-use link</p>

{_fallback_link(link)}"""


def _reminder_body_html(company_name, intake_link):
    """Short 'gentle reminder' fragment for the branded base template (same CTA as the invite)."""
    name = html.escape((company_name or "there").strip())
    link = html.escape(intake_link, quote=True)
    return f"""\
<p style="margin:0 0 12px;{_FF}font-size:20px;line-height:1.3;font-weight:700;color:#0f172a;">A quick reminder, {name} 👋</p>
<p style="margin:0 0 28px;{_FF}font-size:16px;line-height:1.65;color:#475569;">We haven&rsquo;t received your onboarding details yet. Whenever you&rsquo;re ready, it only takes a minute to set up your Drivetrain workspace below.</p>
{_cta_button(link)}
<p style="margin:0 0 28px;text-align:center;{_FF}font-size:13px;color:#94a3b8;">Secure, single-use link</p>
{_fallback_link(link)}"""


class SendgridClient:
    """SendGrid email client. Inject `sg_client` (a SendGridAPIClient) for tests."""

    def __init__(self, sg_client=None):
        self._sg_client = sg_client

    def _send(self, recipient, body_html, *, cc=(), headers=()):
        """Build + send one branded base-template email (subject + body injected). Raises
        ConfigError (no key) / ServiceError (send failed) — the caller decides fatal vs best-effort."""
        if not settings.sendgrid_api_key:
            raise ConfigError("SendGrid: SENDGRID_API_KEY not set")
        message = Mail(from_email=From(constants.SENDGRID_FROM_EMAIL, constants.SENDGRID_FROM_NAME),
                       to_emails=str(recipient))
        for addr in cc:
            message.add_cc(Cc(addr))
        message.template_id = BASE_EMAIL_TEMPLATE_ID  # shared base template; content HTML built in code
        message.dynamic_template_data = {'subject': _SUBJECT, 'body': body_html}
        for header in headers:
            message.add_header(header)
        sg = self._sg_client or SendGridAPIClient(settings.sendgrid_api_key)
        try:
            sg.client.timeout = _SEND_TIMEOUT  # fail fast instead of blocking the thread on a hung send
        except AttributeError:
            pass  # an injected test client may not expose .client
        try:
            resp = sg.send(message)
        except Exception as e:  # noqa: BLE001
            raise ServiceError("SendGrid", str(e))
        return resp.status_code

    def send_intake_email(self, koid, data, intake_link):
        """Send the intake invite. The caller mints the token + builds intake_link inside the register
        transaction; this only sends. Raises ConfigError/ServiceError so the register transaction rolls back."""
        recipient = data.get('onboarding_contact_email')
        if not recipient:
            raise ValueError("onboarding_contact_email is required to send the intake email")
        # CC the AE who registered (registered_by) + any standing CCs (env-resolved). Skip any cc that
        # equals the recipient (SendGrid rejects a duplicate To/CC address) and dedupe.
        cc_list = list(recipients.requisition_cc)
        ae = data.get('registered_by')
        if ae:
            cc_list.append(ae)
        cc = list(dict.fromkeys(c for c in cc_list if c and c != recipient))
        status = self._send(recipient, _intake_body_html(data.get('company_name'), intake_link),
                            cc=cc, headers=[Header("Message-ID", intake_msgid(koid))])  # anchor for threaded reminders
        return f"sendgrid {status}; base template {BASE_EMAIL_TEMPLATE_ID}"

    def send_intake_reminder_email(self, koid, data, intake_link, seq: int):
        """Send reminder #seq (1-based) to the customer, threaded under the original intake email via
        RFC 5322 Message-ID/In-Reply-To/References + an identical subject (Gmail threading).
        Raises ConfigError/ServiceError on failure (caller treats it as best-effort)."""
        recipient = data.get('onboarding_contact_email')
        if not recipient:
            raise ValueError("onboarding_contact_email is required to send the reminder email")
        # ancestry oldest→first: <intake>, <reminder-1> ... <reminder-(seq-1)>; parent = the last of those.
        references = [intake_msgid(koid)] + [reminder_msgid(koid, i) for i in range(1, seq)]
        headers = [Header("Message-ID", reminder_msgid(koid, seq)),
                   Header("In-Reply-To", references[-1]),
                   Header("References", " ".join(references))]
        status = self._send(recipient, _reminder_body_html(data.get('company_name'), intake_link), headers=headers)
        return f"sendgrid {status}; reminder {seq}"


sendgrid_client = SendgridClient()
