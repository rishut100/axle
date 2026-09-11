"""AWS IAM Identity Center — Identity Store API (user lifecycle) + SSO Admin API (permission-set
assignment on a target account). Auth reuses this machine's/pod's existing AWS credential chain
(boto3 default — same one main.py's other boto3 clients already use), not a separate access key
setting. Real, valid AWS credentials were confirmed present on this dev machine during testing
(main.py's boto3 clients picked them up from ~/.aws/credentials) — this client hasn't been
credential-blocked the way the OAuth/token-based ones are, only untested against a live account yet.
"""
import logging

import boto3
from botocore.exceptions import ClientError

from dtapp.garage.core.config import settings
from dtapp.garage.core.errors import ConfigError, ServiceError

logger = logging.getLogger(__name__)


class AwsIamClient:
    def __init__(self, identitystore_client=None, sso_admin_client=None):
        self._identitystore = identitystore_client
        self._sso_admin = sso_admin_client

    def _configured(self) -> bool:
        return bool(settings.aws_identity_store_id and settings.aws_identity_center_instance_arn)

    def _dryrun(self, op, payload):
        logger.info("[access] DRYRUN aws_iam %s — would send: %s", op, payload)
        return {"dryrun": True, "op": op}

    def _require_config(self):
        if self._configured():
            return False
        if settings.dryrun_unconfigured:
            return True
        raise ConfigError("AwsIam: aws_identity_store_id/aws_identity_center_instance_arn not configured")

    def _identitystore_client(self):
        if self._identitystore is None:
            self._identitystore = boto3.client("identitystore", region_name=settings.aws_region)
        return self._identitystore

    def _sso_admin_client(self):
        if self._sso_admin is None:
            self._sso_admin = boto3.client("sso-admin", region_name=settings.aws_region)
        return self._sso_admin

    def _find_user_id(self, email: str):
        try:
            resp = self._identitystore_client().list_users(
                IdentityStoreId=settings.aws_identity_store_id,
                Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
            )
            users = resp.get("Users", [])
            return users[0]["UserId"] if users else None
        except ClientError as e:
            raise ServiceError("AwsIam", str(e))

    def invite_member(self, email: str, name: str = "") -> dict:
        """Create-or-reuse the Identity Center user, then assign the configured permission set on
        aws_target_account_id — mirrors the real workflow (Rishu manages exactly this pairing, per
        #tool-access-poc history)."""
        if self._require_config():
            return self._dryrun("invite_member", {"email": email})
        try:
            user_id = self._find_user_id(email)
            if not user_id:
                first, _, last = (name or email.split("@")[0]).partition(" ")
                resp = self._identitystore_client().create_user(
                    IdentityStoreId=settings.aws_identity_store_id, UserName=email,
                    Name={"GivenName": first or email.split("@")[0], "FamilyName": last or "."},
                    Emails=[{"Value": email, "Primary": True}],
                )
                user_id = resp["UserId"]
            if settings.aws_target_account_id and settings.aws_permission_set_arn:
                self._sso_admin_client().create_account_assignment(
                    InstanceArn=settings.aws_identity_center_instance_arn,
                    TargetId=settings.aws_target_account_id, TargetType="AWS_ACCOUNT",
                    PermissionSetArn=settings.aws_permission_set_arn,
                    PrincipalType="USER", PrincipalId=user_id,
                )
            return {"id": user_id}
        except ClientError as e:
            raise ServiceError("AwsIam", str(e))

    def remove_member(self, email: str) -> None:
        """Removes the account assignment (revokes the permission set on the target account) — does
        NOT delete the underlying Identity Center user, since they may still need it for other
        accounts/apps. Matches this tool's soft-deprovision convention elsewhere."""
        if self._require_config():
            self._dryrun("remove_member", {"email": email})
            return
        user_id = self._find_user_id(email)
        if not user_id or not (settings.aws_target_account_id and settings.aws_permission_set_arn):
            return
        try:
            self._sso_admin_client().delete_account_assignment(
                InstanceArn=settings.aws_identity_center_instance_arn,
                TargetId=settings.aws_target_account_id, TargetType="AWS_ACCOUNT",
                PermissionSetArn=settings.aws_permission_set_arn,
                PrincipalType="USER", PrincipalId=user_id,
            )
        except ClientError as e:
            raise ServiceError("AwsIam", str(e))

    def get_user_status(self, email: str) -> str:
        if self._require_config():
            return "removed"
        user_id = self._find_user_id(email)
        if not user_id:
            return "removed"
        if not (settings.aws_target_account_id and settings.aws_permission_set_arn):
            return "active"  # user exists; no assignment configured to check
        try:
            resp = self._sso_admin_client().list_account_assignments(
                InstanceArn=settings.aws_identity_center_instance_arn,
                AccountId=settings.aws_target_account_id, PermissionSetArn=settings.aws_permission_set_arn,
            )
            return "active" if any(a["PrincipalId"] == user_id for a in resp.get("AccountAssignments", [])) else "removed"
        except ClientError as e:
            raise ServiceError("AwsIam", str(e))


aws_iam_client = AwsIamClient()
