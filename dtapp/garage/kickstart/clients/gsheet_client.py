import json
import logging
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from dtapp.garage.core import constants
from dtapp.garage.core.config import settings
from dtapp.garage.kickstart.schemas.clients import ConsultantRow

logger = logging.getLogger(__name__)
_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


@lru_cache(maxsize=1)
def _credentials():
    creds_dict = json.loads(settings.google_service_account_json)
    return service_account.Credentials.from_service_account_info(creds_dict, scopes=[_SCOPE])


class GSheetClient:
    """Google Sheets reader (consultant roster). Inject `service` (a Sheets API client) for tests."""

    def __init__(self, service=None):
        self._service_override = service

    def _service(self):
        if self._service_override is not None:
            return self._service_override
        return build("sheets", "v4", credentials=_credentials(), cache_discovery=False, static_discovery=True)

    def read_consultant_rows(self) -> list[ConsultantRow]:
        """Return [ConsultantRow(name, email, department), ...] from the consultant roster sheet.

        Fail-soft: returns [] if SA creds are blank, or on any API error.
        Reads constants.CONSULTANT_SHEET_TAB ('All').
        department is "" when no department column is present.
        """
        if not settings.google_service_account_json:
            logger.warning("GSheet consultant sync skipped: GOOGLE_SERVICE_ACCOUNT_JSON not set")
            return []
        try:
            svc = self._service()
            sheet_id = constants.CONSULTANT_SHEET_ID
            tab = constants.CONSULTANT_SHEET_TAB

            resp = svc.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!A:Z",
            ).execute()
            rows = resp.get("values", [])
            if not rows:
                return []

            header = [str(h).strip().lower() for h in rows[0]]
            # Detect name/email columns by header keyword; fall back to col A / col B.
            name_idx = next((i for i, h in enumerate(header) if "name" in h), 0)
            email_idx = next((i for i, h in enumerate(header) if "email" in h), 1)
            # department is optional — no fallback; absent → "" per row.
            dept_idx = next((i for i, h in enumerate(header) if "department" in h), None)

            result = []
            for row in rows[1:]:
                email = row[email_idx].strip().lower() if email_idx < len(row) else ""
                if not email:
                    continue
                name = row[name_idx].strip() if name_idx < len(row) else email
                department = row[dept_idx].strip() if dept_idx is not None and dept_idx < len(row) else ""
                result.append(ConsultantRow(name=name or email, email=email, department=department))
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("GSheet consultant sync failed: %s", e)
            return []


gsheet_client = GSheetClient()
