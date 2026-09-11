from dtapp.main import *
from dtapp.services.database_service import *
from dtapp.services.email_service import (
    send_tenant_access_approval_confirmation_mail,
    send_tenant_access_approval_mail,
    send_tenant_access_approval_mail_v3,
)
from dtapp.services.slack_service import post_tenant_access_request
from dtapp.views.bigquery_access import request_dtx_springs_bq_access

EU_AXLE_BASE_URL = "https://admin.eu.example.internal"


def _is_eu_tenant(tenant_id):
    try:
        _tenant_id = int(tenant_id)
        return _tenant_id == 704 or _tenant_id > 5000
    except (TypeError, ValueError):
        return False


def _forward_to_eu_axle_access_create(json_data):
    headers = {'Content-Type': 'application/json', 'x-access-token': os.environ.get('EU_AXLE_TOKEN')}
    response = requests.post(f"{EU_AXLE_BASE_URL}/tenant_access/create", json=json_data, headers=headers)
    return response.json()


def _forward_to_eu_axle_access_update(json_data):
    headers = {'Content-Type': 'application/json', 'x-access-token': os.environ.get('EU_AXLE_TOKEN')}
    response = requests.patch(f"{EU_AXLE_BASE_URL}/tenant_access/update", json=json_data, headers=headers)
    return response.json()


def _db_query(query, update=False, v3=False):
    if v3:
        return execute_v3_db_query(query, update=update)
    return execute_db_query(query, update=update)


def _retool_link(id, v3=False):
    app_name = "tenant_access_v3" if v3 else "tenant_access"
    return f"https://your-retool-domain.retool.com/app/{app_name}#id={id}"


def validate_tenant_information(tenant_id, v3=False):
    try:
        if int(tenant_id):
            app.logger.info("Tenant id is int")

    except Exception as e:
        app.logger.info("Tenant id is not int. Getting tenant id from tenants tables")
        query = f"select schema from public.tenants where subdomain = '{tenant_id}'"
        app.logger.info(query)
        qry_result = _db_query(query, v3=v3)

        for v in qry_result:
            app.logger.info(v["schema"])
            tenant_id = v["schema"]

    return tenant_id


def validate_validity(validity):
    _validity = datetime.strptime(validity.split(".")[0], "%Y-%m-%dT%H:%M:%S")
    seven_days = datetime.today() + timedelta(days=2)

    if _validity >= seven_days:
        return False
    else:
        return True


def validate_reason(reason):
    return bool(re.search(r"^[a-zA-Z]{3}", reason))


def update_tenant_access(ids, status, approved_by, v3=False):
    if not isinstance(ids, list):
        ids = [ids]

    reason = "Approved" if status == 1 else "Rejected" if status == 2 else ""
    result_list = []

    # Redirect to EU
    if any(int(id) < 55000 for id in ids) and (app_env or '').lower() != 'prod-eu':
        app.logger.info(f"Forwarding tenant access approval to EU axle")
        json_data = {
            "ids": ids,
            "status": status,
            "approved_by": approved_by,
        }
        return _forward_to_eu_axle_access_update(json_data)

    for id in ids:
        query = f"update public.tenant_access set status = {status}, approved_by = '{approved_by}', updated_at=now() where id = {id} RETURNING *;"
        app.logger.info(query)
        qry_result = _db_query(query, update=True, v3=v3)

        # Updating the same permission to sandbox tenant
        query_sb = f"insert into public.tenant_access (tenant_id, user_email, status, validity, reason, approved_by) values (100{qry_result['tenant_id']}, '{qry_result['user_email']}', {status}, '{qry_result['validity']}', '{qry_result['reason']}', '{approved_by}') RETURNING *;"
        app.logger.info(query_sb)
        _db_query(query_sb, update=True, v3=v3)

        result_list.append({
            "id": qry_result["id"],
            "tenant_id": qry_result["tenant_id"],
            "user_email": qry_result["user_email"],
            "validity": qry_result["validity"].strftime("%Y-%m-%d %H:%M:%S"),
            "reason": qry_result["reason"],
            "approved_by": qry_result["approved_by"],
            "status": qry_result["status"],
        })

        if (app_env or '').lower() != 'prod-eu' and (status == 1 and v3):
            bq_payload = {
                "email": qry_result["user_email"],
                "tenant_id": qry_result["tenant_id"],
                "reason": qry_result["reason"],
                "validity": qry_result["validity"].strftime("%Y-%m-%dT%H:%M:%S"),
            }
            app.logger.info(f"Granting BQ access for tenant_id={qry_result['tenant_id']}, user_email={qry_result['user_email']}")
            request_dtx_springs_bq_access(bq_payload, auto_approve=True)

        send_tenant_access_approval_confirmation_mail(
            tenant_id=qry_result["tenant_id"],
            user_email=qry_result["user_email"],
            reason=reason,
        )

    app.logger.info(result_list)
    return result_list


def create_tenant_access(tenant_id, user_email, validity, reason, v3=False):
    result = {}
    result_list = []
    app.logger.info(
        f"Tenant access for {tenant_id} - {user_email} - {validity} - {reason}"
    )

    # redirect to EU
    if _is_eu_tenant(tenant_id) and (app_env or '').lower() != 'prod-eu':
        app.logger.info(f"Forwarding tenant access create for tenant_id={tenant_id} to EU axle")
        json_data = {
            "tenant_id": tenant_id,
            "user_email": user_email,
            "validity": validity,
            "reason": reason,
        }
        return _forward_to_eu_axle_access_create(json_data)

    bq_payload = {
        "email": user_email,
        "tenant_id": tenant_id,
        "reason": reason,
        "validity": validity,
    }

    # validate tenant id
    tenant_id = validate_tenant_information(tenant_id, v3=v3)

    try:
        if int(tenant_id):
            query = f"insert into public.tenant_access (tenant_id, user_email, validity, reason) values ({tenant_id}, '{user_email}', '{validity}', '{reason}') RETURNING *;"
            app.logger.info(query)
            qry_result = _db_query(query, update=True, v3=v3)

            result["id"] = qry_result["id"]
            result["tenant_id"] = qry_result["tenant_id"]
            result["user_email"] = qry_result["user_email"]
            result["validity"] = qry_result["validity"].strftime("%Y-%m-%d %H:%M:%S")
            result["reason"] = qry_result["reason"]
            result["status"] = qry_result["status"]
            result_list.append(result.copy())
            app.logger.info(result_list)

            # check for auto approval
            is_auto_approve = (int(tenant_id) > 1000 and int(tenant_id) < 5000) or ((validate_validity(validity) and validate_reason(reason)) and (qry_result["user_email"] in leads_list))
            if is_auto_approve:
                app.logger.info("Tenant access request is for > 1000 tenant or by the leads. Going with auto approval")
                update_tenant_access(result["id"], 1, "auto_approval@example.com", v3=v3)
                return result_list

            else:
                # post Slack notification for manual approval
                post_tenant_access_request(
                    request_id=result["id"],
                    tenant_id=tenant_id,
                    user_email=user_email,
                    validity=validity,
                    reason=reason,
                    v3=v3,
                )

            # mirror the same approval decision for dtx-springs BQ access
            # app.logger.info(f"Creating BQ access for tenant_id={tenant_id}, user_email={user_email}")
            # request_dtx_springs_bq_access(bq_payload, auto_approve=is_auto_approve)

        return result_list
    except Exception as e:
        app.logger.error(e)
        raise Exception(str(e))


_STATUS_LABELS = {0: "Pending", 1: "Approved", 2: "Rejected"}


def get_tenant_access(v3=False, id=None, status=None):
    if id is not None:
        query = f"select * from public.tenant_access where id = {id}"
    elif status == 0:
        query = "select * from public.tenant_access where status = 0"
    elif status == 1:
        query = "select * from public.tenant_access where status = 1 and validity >= now()"
    else:
        query = "select * from public.tenant_access"

    app.logger.info(query)
    qry_result = _db_query(query, v3=v3)

    result_list = []
    for v in qry_result:
        row = {
            "id": v["id"],
            "tenant_id": v["tenant_id"],
            "user_email": v["user_email"],
            "validity": v["validity"].strftime("%Y-%m-%d %H:%M:%S"),
            "reason": v["reason"],
            "status": _STATUS_LABELS.get(v["status"], v["status"]),
        }
        if status == 0:
            row["link"] = _retool_link(v["id"], v3=v3)
        result_list.append(row)

    app.logger.info(result_list)
    return result_list
