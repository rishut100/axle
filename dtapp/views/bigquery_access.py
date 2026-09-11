import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil import parser
from google.cloud import bigquery, resourcemanager_v3
from google.cloud.bigquery.enums import EntityTypes
from google.cloud.resourcemanager_v3.services.projects.transports import (
    ProjectsRestTransport,
)
from google.iam.v1 import iam_policy_pb2, options_pb2, policy_pb2
from psycopg2.errors import UniqueViolation

from dtapp.main import *
from dtapp.services.database_service import run_gcp_access_query
from dtapp.services.email_service import (
    send_bigquery_access_approval_confirmation_mail,
    send_bigquery_access_approval_mail,
    send_bigquery_access_rejection_confirmation_mail,
)
from dtapp.views.common_functions import log_exception

ADMIN_EMAIL_DENYLIST = {
    "rishu@drivetrain.ai",
    "tark@drivetrain.ai",
    "kabilan@drivetrain.ai",
    "sateesh@drivetrain.ai"
}

PROJECT_ID_MAP = {
    "Composed Strata": "composed-strata-301915",
    "Drivetrain Staging": "drivetrain-staging",
    "Drivetrain Preprod": "drivetrain-preprod",
    "Dtx Springs": "dtx-springs",
    # "Drive V2": "drive-v2-466113",
    # "Drive V3": "drive-v3-466113",
    # "Drivetrain Integration": "drivetrainintergation",
    # "Radiator Springs": "radiator-springs",
    # "Radiator Springs V3": "radiator-springs-v3",
    # "Cold Springs": "cold-springs-475107",
    # "Ipaas": "ipaas-466114",
    # "Lmt Springs": "lmt-springs",
    # "Middle Earth": "middle-earth-451206",
    # "Preprod Dtx Springs": "preprod-dtx-springs",
    # "Warm Springs": "warm-springs-464304",
    # "Test Express": "automation-report-db"
}


def get_project_id(incoming_project: str) -> str:
    if not incoming_project:
        raise Exception("Project is required")
    incoming_project = incoming_project.strip()
    try:
        return PROJECT_ID_MAP[incoming_project]
    except KeyError:
        raise Exception(f"Unknown Project: {incoming_project}")


def get_datasets(project_id):
    client = bigquery.Client(project=project_id)
    return client.list_datasets()


def get_tenant_datasets(project_id, tenant_id) -> list:
    if project_id == "dtx-springs":
        return [f"tenant{tenant_id}", f"tenant{tenant_id}_src"]
    datasets = get_datasets(project_id)
    tenant_ds = []
    tenant_prefix = f"tenant{tenant_id}"
    for ds in datasets:
        ds_id = ds.dataset_id
        if ds_id == tenant_prefix or ds_id.startswith(f"{tenant_prefix}_"):
            tenant_ds.append(ds_id)
        elif ds_id == str(tenant_id):
            tenant_ds.append(ds_id)
    return tenant_ds


def grant_project_iam_policy(project_id, email):
    gcp_client = resourcemanager_v3.ProjectsClient(transport=ProjectsRestTransport())
    resource = f"projects/{project_id}"

    policy_v3 = iam_policy_pb2.GetIamPolicyRequest(
        resource=resource,
        options=options_pb2.GetPolicyOptions(requested_policy_version=3),
    )
    policy = gcp_client.get_iam_policy(request=policy_v3)
    policy.version = 3

    member = f"user:{email}"

    new_bindings = []
    for b in policy.bindings:
        if b.role == "roles/bigquery.jobUser" and member in b.members:
            if len(b.members) > 1:
                b.members.remove(member)
                new_bindings.append(b)
        else:
            new_bindings.append(b)

    del policy.bindings[:]
    policy.bindings.extend(new_bindings)
    policy.bindings.append(
        policy_pb2.Binding(
            role="roles/bigquery.jobUser",
            members=[member]
        )
    )

    gcp_client.set_iam_policy(
        request={
            "resource": resource,
            "policy": policy,
        }
    )


def grant_ds_acl_access(project_id, tenant_id, dataset_acl_role, email):
    entity_type = EntityTypes.USER_BY_EMAIL
    tenant_ds = get_tenant_datasets(project_id, tenant_id)
    if "write" in dataset_acl_role:
        dataset_acl_role = "WRITER"
    else:
        dataset_acl_role = "READER"
    for ds in tenant_ds:
        app.logger.info(
            f"Granting {dataset_acl_role} access to {email} for dataset: {ds}"
        )
        client = bigquery.Client(project=project_id)
        dataset = client.get_dataset(ds)
        entries = list(dataset.access_entries)
        for entry in entries:
            if (
                entry.role == dataset_acl_role
                and entry.entity_type == entity_type
                and entry.entity_id == email
            ):
                return
        entries.append(
            bigquery.AccessEntry(
                role=dataset_acl_role,
                entity_type=entity_type,
                entity_id=email,
            )
        )
        dataset.access_entries = entries
        client.update_dataset(dataset, ["access_entries"])
        app.logger.info(f"Updated dataset: {dataset.dataset_id} for user: {email} with role: {dataset_acl_role}")


def revoke_ds_acl_access(project_id: str, tenant_id: str, email: str):
    client = bigquery.Client(project=project_id)
    entity_type = EntityTypes.USER_BY_EMAIL

    tenant_ds_ids = get_tenant_datasets(project_id, tenant_id)

    for ds_id in tenant_ds_ids:
        dataset = client.get_dataset(f"{project_id}.{ds_id}")
        entries = list(dataset.access_entries)
        before = len(entries)
        entries = [
            e
            for e in entries
            if not (
                e.entity_type == entity_type
                and e.entity_id == email
                and e.entity_id.lower().endswith("@drivetrain.ai")
            )
        ]  # extra check on the email to check it does not end with drivetrain.ai
        if len(entries) == before:
            app.logger.info(f"[SKIP] {ds_id}: no ACL entry for {email}")
            continue
        dataset.access_entries = entries
        client.update_dataset(dataset, ["access_entries"])
        app.logger.info(f"[REVOKED] {ds_id}: removed ACL for {email}")


def approve_bq_access(request_id, approved_by: str):
    row = run_gcp_access_query(
        """
        SELECT *
        FROM bq_access_requests
        WHERE request_id = %(id)s
        FOR UPDATE
        """,
        {"id": request_id},
        fetch=True,
    )
    if not row:
        raise Exception("Request not found")

    req = row[0]
    if req["status"] == 1:
        return {"message": "already approved", "request_id": request_id}
    if req["status"] == 2 or req["revoked"] == 1:
        raise Exception("Request already rejected or revoked")
    if req["expires_at"] <= datetime.utcnow():
        raise Exception("Request already expired")

    project_id = req["project_id"]
    tenant_id = req["tenant_id"]
    email = req["email"]
    access_level = req["access_level"]
    grant_project_iam_policy(project_id, email)
    grant_ds_acl_access(project_id, tenant_id, access_level, email)

    run_gcp_access_query(
        """
        UPDATE bq_access_requests
        SET status = 1,
            approved_by = %(approved_by)s,
            revoked = 0,
            created_at = now()
        WHERE request_id = %(id)s
        """,
        {
            "id": request_id,
            "approved_by": approved_by,
        },
        fetch=False,
    )
    send_bigquery_access_approval_confirmation_mail(
        req["tenant_id"], req["email"], req["reason"]
    )
    return {
        "message": "granted",
        "expires_at": req["expires_at"],
        "request_id": req["request_id"],
        "email": email,
        "request_id": request_id,
    }


def reject_bq_access(request_id, rejected_by, rejection_reason=None):
    row = run_gcp_access_query(
        """
        SELECT *
        FROM bq_access_requests
        WHERE request_id = %(id)s
        FOR UPDATE
        """,
        {"id": request_id},
        fetch=True,
    )
    if not row:
        raise Exception("Request not found")
    req = row[0]
    if req["status"] == 2:
        return {"message": "already rejected", "request_id": request_id}
    if req["status"] == 1:
        raise Exception("Cannot reject an approved request")

    run_gcp_access_query(
        """
        UPDATE bq_access_requests
        SET status = 2,
            revoked = 1,
            approved_by = %(rejected_by)s,
            created_at = now(),
            expires_at = now(),
            reason = %(reason)s
        WHERE request_id = %(id)s
        """,
        {
            "id": request_id,
            "rejected_by": rejected_by,
            "reason": rejection_reason or "Rejected",
        },
        fetch=False,
    )
    send_bigquery_access_rejection_confirmation_mail(
        req["tenant_id"], req["email"], rejection_reason or "Rejected"
    )
    return {"message": "rejected", "request_id": request_id, "email": req["email"]}


def request_bq_access(payload):
    try:
        project_id = get_project_id(payload["project_id"])
        email = payload["email"]
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            raise Exception(
                "tenant_id is required (e.g. '1001' or an exact dataset id)"
            )
        access_level = payload.get("access_level")
        reason = payload.get("reason", "")
        validity_dt = parser.isoparse(payload["validity"])

        if validity_dt.tzinfo is None:
            validity_dt = validity_dt.replace(tzinfo=timezone.utc)
        else:
            validity_dt = validity_dt.astimezone(timezone.utc)

        expires_at = validity_dt.replace(tzinfo=None)
        validity_str = validity_dt.strftime("%Y-%m-%d %H:%M:%S")  # for sending response

        if expires_at <= datetime.utcnow():
            raise Exception("Please Enter a future validity date/time")

        email = email.strip().lower()
        if email in ADMIN_EMAIL_DENYLIST or email.endswith(".gserviceaccount.com"):
            raise Exception(f"cannot proceed: {email} is an admin/service account")
        record = {
            "request_id": str(uuid.uuid4()),
            "project_id": project_id,
            "email": email,
            "tenant_id": str(tenant_id),
            "access_level": access_level,
            "approved_by": "",
            "reason": reason,
            "expires_at": expires_at,
            "created_at": datetime.utcnow(),
            "revoked": 0,
            "status": 0,
        }

        # Check for valid bq dataset
        tenant_ds = get_tenant_datasets(project_id, tenant_id)
        if not tenant_ds:
            raise Exception(f"No valid bq dataset found for tenant_id: {tenant_id}")

        run_gcp_access_query(
            """
            INSERT INTO bq_access_requests
            (request_id, project_id, email, tenant_id, access_level, approved_by, reason, expires_at, created_at, revoked, status)
            VALUES (%(request_id)s, %(project_id)s, %(email)s, %(tenant_id)s, %(access_level)s, %(approved_by)s, %(reason)s, %(expires_at)s, %(created_at)s, %(revoked)s, %(status)s)
            """,
            record,
            fetch=False,
        )
        if (expires_at - datetime.utcnow()) <= timedelta(days=1):
            record["approved_by"] = "auto_approval@drivetrain.ai"
            return approve_bq_access(record["request_id"], record["approved_by"])
        else:
            send_bigquery_access_approval_mail(
                id=record["request_id"],
                tenant_id=record["tenant_id"],
                user_email=record["email"],
                validity=validity_str,
                reason=record["reason"],
            )

        return {
            "message": "Please wait while your Big Query access is being reviewd",
            "expires_at": record["expires_at"],
            "request_id": record["request_id"],
            "email": email,
        }
    except UniqueViolation:
        return {
            "error": "User: "
            + email
            + " already has active access request for the Project: "
            + project_id
            + " and level"
        }, 409


def get_bq_access(id):
    result = {}
    result_list = []
    query = f"select * from public.bq_access_requests where request_id = {id}"
    app.logger.info(query)
    qry_result = run_gcp_access_query(
        """
        SELECT *
        FROM bq_access_requests
        WHERE request_id = %(request_id)s
        """,
        {"request_id": id},
        fetch=True,
    )
    for v in qry_result:
        result["id"] = v["request_id"]
        result["tenant_id"] = v["tenant_id"]
        result["email"] = v["email"]
        result["expires_at"] = v["expires_at"].isoformat()
        result["reason"] = v["reason"]
        result["status"] = v["status"]
        result_list.append(result.copy())
    app.logger.info(result_list)
    return result_list


def revoke_expired_access():
    now_utc = datetime.utcnow()
    rows = (
        run_gcp_access_query(
            """
        SELECT request_id, project_id, email, tenant_id
        FROM bq_access_requests
        WHERE revoked = 0
        AND status = 1
        AND expires_at <= %(now)s
        """,
            {"now": now_utc},
            fetch=True,
        )
        or []
    )
    for r in rows:
        request_id = r["request_id"]
        project_id = r["project_id"]
        email = r["email"]
        tenant_id = r["tenant_id"]

        revoke_ds_acl_access(project_id, tenant_id, email)
        run_gcp_access_query(
            """
            UPDATE bq_access_requests
            SET revoked = 1,
                status = 0
            WHERE request_id = %(request_id)s
            """,
            {"request_id": request_id},
            fetch=False,
        )
        app.logger.info(f"[DONE] request_id={request_id} revoked")


def approve_dtx_springs_bq_access(
    request_id, project_id, tenant_id, email, approved_by: str
):
    access_level = "readonly"
    grant_project_iam_policy(project_id, email)
    grant_ds_acl_access(project_id, tenant_id, access_level, email)

    run_gcp_access_query(
        """
        UPDATE bq_access_requests
        SET status = 1,
            approved_by = %(approved_by)s,
            revoked = 0,
            created_at = now()
        WHERE request_id = %(id)s
        """,
        {
            "id": request_id,
            "approved_by": approved_by,
        },
        fetch=False,
    )
    send_bigquery_access_approval_confirmation_mail(tenant_id, email, "Approved")
    return {
        "message": "granted",
        "request_id": request_id,
        "email": email,
    }


def request_dtx_springs_bq_access(payload, auto_approve=False):
    try:
        project_id = "dtx-springs"
        email = payload["email"]
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            raise Exception(
                "tenant_id is required (e.g. '1001' or an exact dataset id)"
            )
        access_level = "readonly"
        reason = payload.get("reason", "")
        validity_dt = parser.isoparse(payload["validity"])

        if validity_dt.tzinfo is None:
            validity_dt = validity_dt.replace(tzinfo=timezone.utc)
        else:
            validity_dt = validity_dt.astimezone(timezone.utc)

        expires_at = validity_dt.replace(tzinfo=None)

        record = {
            "request_id": str(uuid.uuid4()),
            "project_id": project_id,
            "email": email,
            "tenant_id": str(tenant_id),
            "access_level": access_level,
            "approved_by": "",
            "reason": reason,
            "expires_at": expires_at,
            "created_at": datetime.utcnow(),
            "revoked": 0,
            "status": 0,
        }

        run_gcp_access_query(
            """
            INSERT INTO bq_access_requests
            (request_id, project_id, email, tenant_id, access_level, approved_by, reason, expires_at, created_at, revoked, status)
            VALUES (%(request_id)s, %(project_id)s, %(email)s, %(tenant_id)s, %(access_level)s, %(approved_by)s, %(reason)s, %(expires_at)s, %(created_at)s, %(revoked)s, %(status)s)
            """,
            record,
            fetch=False,
        )

        if auto_approve:
            record["approved_by"] = "auto_approval@drivetrain.ai"
            approve_dtx_springs_bq_access(
                record["request_id"],
                record["project_id"],
                record["tenant_id"],
                record["email"],
                record["approved_by"],
            )

        return "Access granted"
    except UniqueViolation:
        return {
            "error": "User: "
            + email
            + " already has active access request for the Project: "
            + project_id
            + " and level"
        }, 409
