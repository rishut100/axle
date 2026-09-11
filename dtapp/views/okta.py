from dtapp.constants import *
from dtapp.services.database_service import *
from flask import g

okta_api_key = os.environ.get('OKTA_API_TOKEN')


def get_okta_user_groups(user_id: int):
    fetch_user_group_url = f"{PRODUCT_OKTA}/users/{user_id}/groups"
    headers = {'Accept': 'application/json',
               'Content-Type': 'application/json',
               'Authorization': f'SSWS {okta_api_key}'}

    group_ids = requests.get(url=fetch_user_group_url, headers=headers).json()

    tenants = []
    for x in group_ids:
        if x['profile']['name'].isdigit():
            tenant = str(x['profile']['name'])
            tenants.append(tenant)

    return tenants


def terminate_existing_sessions(tenant_id: int, user_email: str):
    if (tenant_id >= 400 and tenant_id <= 900) or (tenant_id > 2000):
        app.logger.info(f"Logging out of all sessions from v3 tenant {tenant_id} for username {user_email}")
        execute_v3_db_query(f'update "{tenant_id}".users set signed_out_at=now() where username=\'{user_email}\' RETURNING id, username;', True)
    else:
        app.logger.info(f"Logging out of all sessions from v2 tenant {tenant_id} for username {user_email}")
        execute_db_query(f'update "{tenant_id}".users set signed_out_at=now() where username=\'{user_email}\' RETURNING id, username;', True)


def okta_password_reset(event_data):
    user_id = event_data[0]['actor']['id']
    user_email = event_data[0]['actor']['alternateId']
    user_name = event_data[0]['actor']['displayName']
    client_ip = event_data[0]['client']['ipAddress']
    
    # Store client_ip in Flask's g object for logging
    g.client_ip = client_ip
    
    tenant = ""

    # Getting tenant from group details
    url = f"{PRODUCT_OKTA}/users/{user_id}/groups"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SSWS {okta_api_key}"
    }

    api_response = requests.get(url=url, headers=headers)

    app.logger.info("Finding associated tenants for user %s", user_name)
    for x in json.loads(api_response.text):
        try:
            okta_group = x['profile']['name']
            app.logger.info("okta_group - %s", okta_group)
            if okta_group.isdigit():
                tenant = int(okta_group)
                app.logger.info("Found tenant ID %s", tenant)

        except Exception as err:
            app.logger.warning(f"Exception occurred at tenant ID {tenant} {err}")
            continue

        if tenant != "":
            terminate_existing_sessions(tenant, user_email)

    return