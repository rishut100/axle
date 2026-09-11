"""GCP IAM — project-level role bindings via google-cloud-resource-manager. No separate "user account"
to create — GCP IAM just binds an existing Google identity's email to a role; the identity itself comes
from Google Workspace (see gworkspace_client.py), so this client only ever grants/revokes the BINDING.

Engineers are only ever GRANTED GCP access on staging + preprod (never prod) — invite_member/
get_user_status stay scoped to exactly those two projects.

REVOKE additionally runs a dynamic sweep across every project the credential can see, not just
staging+preprod (decision 2026-09-07): there's no GCP Organization resource in this setup at all (no
folder-level IAM inheritance is possible), and new preprod-style projects get created over time, so a
fixed 2-project list can't guarantee a leaver's binding is actually gone everywhere it might exist.

ONE credential (settings.gcp_iam_service_account_json) covers all of this — consolidated 2026-09-07
after finding the existing service account already has real, standing access to staging, preprod, and
several other projects (added project-by-project by whoever set it up, since there's no org to grant
on). Distinct from google_service_account_json (BigQuery/Workspace, a narrower identity)."""
import json
import logging

from google.cloud import resourcemanager_v3
from google.oauth2 import service_account

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _matching_members(members, email: str) -> list:
    """A binding's member string for this email isn't always exactly 'user:{email}' — once the
    underlying Google identity is deleted (e.g. by G-Suite/Workspace deprovisioning), GCP marks the
    binding as an orphaned reference: 'deleted:user:{email}?uid=<numeric-id>' — BOTH a 'deleted:'
    prefix AND a '?uid=' suffix, confirmed real 2026-09-07 (a leaver's stale BigQuery role bindings
    were in exactly this form and missed twice — once by an exact-match check, once by a fix that only
    handled the suffix). Match both the live and orphaned forms."""
    forms = (f"user:{email}", f"deleted:user:{email}")
    return [m for m in members if any(m == f or m.startswith(f + "?") for f in forms)]


class GcpIamClient:
    def __init__(self, project_id_attrs=None):
        self._project_id_attrs = project_id_attrs or [
            ("staging", "gcp_project_id"),
            ("preprod", "gcp_preprod_project_id"),
        ]
        self._credentials = None
        self._client_cache = None

    def _targets(self):
        return [(label, getattr(settings, attr)) for label, attr in self._project_id_attrs
                if getattr(settings, attr)]

    def _configured(self) -> bool:
        return bool(settings.gcp_iam_service_account_json) and bool(self._targets())

    def _client(self):
        if self._client_cache is not None:
            return self._client_cache
        if self._credentials is None:
            info = json.loads(settings.gcp_iam_service_account_json)
            self._credentials = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
        self._client_cache = resourcemanager_v3.ProjectsClient(credentials=self._credentials)
        return self._client_cache

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN gcp_iam %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("GcpIam: gcp_iam_service_account_json/project ids not configured")

    def _sweep_all_projects(self, email: str) -> dict:
        """Best-effort: list every project the credential can see, strip `email` from EVERY role
        binding on each one it has write access to (not just gcp_default_role — a leaver may have
        accumulated other roles, e.g. BigQuery-specific ones, granted outside this module entirely;
        confirmed real 2026-09-07). Most projects will fail on read (no standing access there) — that's
        expected, not logged as an error; only a project we COULD read but failed to write is worth
        surfacing, since that's a real, unexpected problem."""
        client = self._client()
        swept, no_access = [], 0
        for project in client.search_projects():
            resource = f"projects/{project.project_id}"
            try:
                policy = client.get_iam_policy(request={"resource": resource})
            except Exception:  # noqa: BLE001 — no read access on this project, skip silently
                no_access += 1
                continue
            changed = False
            for binding in policy.bindings:
                for m in _matching_members(binding.members, email):
                    binding.members.remove(m)
                    changed = True
            if changed:
                try:
                    client.set_iam_policy(request={"resource": resource, "policy": policy})
                    swept.append(project.project_id)
                except Exception as e:  # noqa: BLE001 — had read access, write failed — worth knowing
                    logger.warning("[access] gcp_iam sweep: found binding but couldn't remove it on %s: %s",
                                   project.project_id, e)
        return {"swept": swept, "skipped_no_access": no_access}

    def invite_member(self, email: str) -> dict:
        """Read-modify-write each configured project's IAM policy — GCP requires this pattern (no
        single 'add one binding' call); best-effort per project so staging succeeding doesn't depend
        on preprod also succeeding (and vice versa) — a partial grant is still reported, not silently
        swallowed, so the checklist reflects reality."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        client = self._client()
        granted, errors = [], {}
        for label, project_id in self._targets():
            resource = f"projects/{project_id}"
            try:
                policy = client.get_iam_policy(request={"resource": resource})
                member = f"user:{email}"
                for binding in policy.bindings:
                    if binding.role == settings.gcp_default_role:
                        if member not in binding.members:
                            binding.members.append(member)
                        break
                else:
                    policy.bindings.append({"role": settings.gcp_default_role, "members": [member]})
                client.set_iam_policy(request={"resource": resource, "policy": policy})
                granted.append(label)
            except Exception as e:  # noqa: BLE001 — recorded per-project, not raised, so one bad project
                errors[label] = str(e)                  # never blocks granting on the other
        if not granted and errors:
            raise ServiceError("GcpIam", f"failed on every configured project: {errors}")
        return {"granted": email, "role": settings.gcp_default_role, "projects": granted, "errors": errors or None}

    def remove_member(self, email: str) -> dict:
        """Strips `email` from EVERY role binding on staging+preprod, not just gcp_default_role — see
        _sweep_all_projects' docstring for why (a real leaver had unrelated BigQuery roles this module
        never granted, missed entirely until this was widened)."""
        if self._require_config():
            return self._dryrun("remove_member", {"email": email})
        client = self._client()
        errors = {}
        for label, project_id in self._targets():
            resource = f"projects/{project_id}"
            try:
                policy = client.get_iam_policy(request={"resource": resource})
                for binding in policy.bindings:
                    for m in _matching_members(binding.members, email):
                        binding.members.remove(m)
                client.set_iam_policy(request={"resource": resource, "policy": policy})
            except Exception as e:  # noqa: BLE001
                errors[label] = str(e)
        sweep_result = self._sweep_all_projects(email)
        if sweep_result["swept"]:
            logger.info("[access] gcp_iam sweep removed %s from: %s", email, sweep_result["swept"])
        if errors and len(errors) == len(self._targets()) and not sweep_result["swept"]:
            raise ServiceError("GcpIam", f"failed on: {errors}")  # every mechanism we had failed outright
        return {"errors": errors or None, **sweep_result}

    def get_user_status(self, email: str) -> str:
        """'active' if ANY role binding on ANY configured project still references this email (not
        just gcp_default_role — see _sweep_all_projects' docstring) — revocation isn't complete until
        it's gone from all of them, in every role it was ever given."""
        if self._require_config():
            return "removed"
        client = self._client()
        for _label, project_id in self._targets():
            try:
                policy = client.get_iam_policy(request={"resource": f"projects/{project_id}"})
                for binding in policy.bindings:
                    if _matching_members(binding.members, email):
                        return "active"
            except Exception as e:  # noqa: BLE001
                raise ServiceError("GcpIam", str(e))
        return "removed"


gcp_iam_client = GcpIamClient()
