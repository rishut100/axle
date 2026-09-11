from dtapp.main import *
from dtapp.services.database_service import *

okta_api_key = os.environ.get('OKTA_API_TOKEN')


def get_active_tenant_list():
    result = {}
    result_list = []

    query = f"select * from public.tenants where (schema >= 200 and schema < 900) and status=1 and subdomain ~ '[A-Za-z]'"
    qry_result = execute_db_query(query)

    for v in qry_result:
        result['tenant_id'] = v['schema']
        result['subdomain'] = v['subdomain']
        result_list.append(result.copy())
    
    app.logger.info(f"Active tenant list: {result_list}")
    
    return result_list


def get_all_tenant_list():
    result = {}
    result_list = []

    query = f"select * from public.tenants"
    qry_result = execute_db_query(query)

    for v in qry_result:
        result['tenant_id'] = v['schema']
        result['subdomain'] = v['subdomain']
        result_list.append(result.copy())
    
    app.logger.info(f"All tenant list: {result_list}")
    
    return result_list


def create_staging_dataset_prod_tenants():
    query = f"select * from public.tenants"
    qry_result = execute_db_query(query)

    for v in qry_result:
        app.logger.info(f"Processing tenant: {v['schema']}")
        tenant = v['schema']
        try:
            if int(tenant) > 100000:
                app.logger.info(f"Ignoring tenant: {tenant}")
            else:
                gcp_client.create_dataset(f"tenant{tenant}_staging", exists_ok=True)
        except Exception as e:
            app.logger.error(f"Error occurred while processing tenant {tenant}: {e}")


def delete_schema(schema_name):
    # query = f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE'"
    # result = execute_db_query(query)
    
    # for table in result:
    #     table_name = table['table_name']
    #     drop_table_query = f'DROP TABLE IF EXISTS "{schema_name}".{table_name} CASCADE'
    #     drop_db_query(drop_table_query)

    drop_schema_query = f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
    drop_db_query(drop_schema_query)


def build_query_to_fetch_records_count(tenant_id):
    return 'SELECT COUNT(*) AS row_count FROM "'+tenant_id+'".metrics ' \
           'UNION ALL ' \
           'SELECT COUNT(*) AS row_count FROM "'+tenant_id+'".connection_instances;'


def is_tenant_reset(tenant_id, bq_schema, is_sandbox_tenant):
    temp = False
    query = build_query_to_fetch_records_count(str(tenant_id))
    result = execute_db_query(query)
    filtered_result = [row for row in result if row['row_count'] > 0]
    
    if filtered_result is not None and len(filtered_result) > 0:
        temp = False
    else:
        temp = True

    ## Primary schema check needs to be done for only non sandbox tenants. Sandbox tenants dataset is built using DBT execution on Cloud DBT IDE
    if not is_sandbox_tenant:
        create_dataset(bq_schema)  ## create dataset if it's not already present.
        try:
            if temp:
                tables = list(gcp_client.list_tables(f"{gcp_client.project}.{bq_schema}"))
                if tables:
                    temp = False
                else:
                    temp = True
        except Exception as e:
            app.logger.info("Dataset not found")
            temp = True
    return temp


def backup_restore_schema(source_id, destination_id, file_name, is_sandbox_tenant):
    ret_backup = 1
    ret_restore = 1
    db_name = app.config['DRIVE_DB']
    db_host = app.config['DRIVE_HOST']
    db_port = app.config['DRIVE_PORT']
    db_user = app.config['DRIVE_USER']
    db_password = db_pass

    ## Sandbox tenants needed to copy all the tables in postgres including connection-instances and connection tables.
    if is_sandbox_tenant:
        app.logger.info(f"DB dump for sandbox tenant {destination_id}")
        cmd = (f"export PGPASSWORD={db_password} && pg_dump -h {db_host} -p {db_port} -d {db_name} -U {db_user} -n {source_id} > {file_name}")
        app.logger.info(cmd)
        ret_backup = subprocess.Popen(cmd, shell=True)
        ret_backup.wait()

    else:
        app.logger.info(f"DB dump for tenant {destination_id}")
        cmd = f"export PGPASSWORD={db_password} && pg_dump -h {db_host} -p {db_port} -d {db_name} -U {db_user} -n {source_id} --exclude-table-data {source_id}.fivetran_groups --exclude-table-data {source_id}.users --exclude-table-data {source_id}.user_roles > {file_name}"
        app.logger.info(cmd)
        ret_backup = subprocess.Popen(cmd, shell=True)
        ret_backup.wait()

    if ret_backup.returncode == 0:
        app.logger.info("pgdump: success")
    else:
        app.logger.info("pgdump: failed")
        return False
    
    sed_cmd = f"sed -i -r 's/\"{source_id}\"/\"{destination_id}\"/g' {file_name} && sed -i -r 's/\"tenant{source_id}\"/\"tenant{destination_id}\"/g' {file_name}"
    app.logger.info(sed_cmd)
    ret_sed = subprocess.Popen(sed_cmd, shell=True)
    ret_sed.wait()

    if ret_sed.returncode == 0:
        # Drop the schema and restore from dump
        delete_schema(destination_id)
        rs_cmd = f"export PGPASSWORD={db_password} && psql -h {db_host} -p {db_port} -d {db_name} -U {db_user} < {file_name} --quiet && rm -rf {file_name}"
        ret_restore = subprocess.Popen(rs_cmd, shell=True)
        ret_restore.wait()

        if ret_restore.returncode == 0:
            app.logger.info("db restore: success")

            # Copying fiscal year to destination tenant
            app.logger.info(f"Copying fiscal year from {source_id} to {destination_id}")
            fsm_query = f"select fiscal_start_month from public.tenants where schema = {source_id};"
            app.logger.info(fsm_query)
            fsm_result = execute_db_query(fsm_query)
            app.logger.info(fsm_result[0]['fiscal_start_month'])

            fsm_upd_query = f"update public.tenants set fiscal_start_month = '{fsm_result[0]['fiscal_start_month']}' where schema = {destination_id} RETURNING *;"
            app.logger.info(fsm_upd_query)
            execute_db_query(fsm_upd_query, update=True)

        else:
            app.logger.info("db restore: failed")
            return False
    else:
        app.logger.info("sed cmd: failed")
        return False

    return True


def copy_primary_dataset(source_id, destination_id, source_bq_schema, destination_bq_schema, is_sandbox_tenant):
    try:
        tables = gcp_client.list_tables(f"{gcp_client.project}.{source_bq_schema}")
        views = []
        create_dataset(destination_bq_schema)
        for table in tables:
            if table.table_type == 'VIEW':
                views.append(table)
                continue
            copy_table(table, source_id, destination_id, destination_bq_schema, is_sandbox_tenant)

        # for view in views:
        #     copy_table(view, destination_id)

    except Exception as e:
        app.logger.error(f"Dataset not found in source tenant {source_id} primary dataset")
        return False

    return True


def copy_lists_table(source_id, destination_id, source_bq_schema, destination_bq_schema, is_sandbox_tenant):
    try:
        tables = gcp_client.list_tables(f"{gcp_client.project}.{source_bq_schema}")
        create_dataset(destination_bq_schema)
        for table in tables:
            if "list_" in table.table_id:
                app.logger.info(f"Copying {table.table_id} to destination dataset {destination_bq_schema}")
                gcp_client.delete_table(f"{destination_bq_schema}.{table.table_id}", not_found_ok = True)
                copy_table(table, source_id, destination_id, destination_bq_schema, is_sandbox_tenant)

    except Exception as e:
        app.logger.error(f"Copy lists table failed. {e}")
        return False

    return True


def copy_table(table, source_id, destination_id, destination_bq_schema, is_sandbox_tenant):
    source_table_id = f'{table.project}.{table.dataset_id}.{table.table_id}'

    if is_sandbox_tenant:
        ## In case of sandbox tenant, don't modify the table name
        temp_destination_id = f'{table.table_id}'
    else:
        temp_destination_id = f'{table.table_id}'.replace(f'tenant{source_id}', f'tenant{destination_id}')

    destination_table_id = f'{table.project}.{destination_bq_schema}.{temp_destination_id}'

    job = gcp_client.copy_table(source_table_id, destination_table_id)
    job.result()


def create_dataset(dataset_name):
    # Create dataset if not exists
    app.logger.info("Creating dataset in Bigquery")
    gcp_client.create_dataset(dataset_name, exists_ok = True)
    app.logger.info("Created dataset successfully")


def check_liquibase_restart():
    action_url = f"https://api.github.com/repos/DrivetrainAi/drive/actions/workflows/prod-liquibase-restart.yml/runs"
    _headers = {'Authorization': f'token {git_token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    
    r = requests.get(action_url, headers=_headers)
    app.logger.info(r.status_code)
    wf_runs_response = json.loads(r.text)['workflow_runs']
    latest_run = wf_runs_response[0]['status']
    
    if latest_run == "in_progress":
        app.logger.error("Liquibase restart already running")
        return False
    
    return True


def check_liquibase_migration():
    action_url = f"https://api.github.com/repos/DrivetrainAi/drive/actions/workflows/prod-liquibase-deployment.yml/runs"
    _headers = {'Authorization': f'token {git_token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    
    r = requests.get(action_url, headers=_headers)
    app.logger.info(r.status_code)
    wf_runs_response = json.loads(r.text)['workflow_runs']
    latest_run = wf_runs_response[0]['status']
    
    if latest_run == "in_progress":
        app.logger.error("Liquibase migration already running")
        return False
    
    return True


def run_liquibase_restart():
    action_url = f"https://api.github.com/repos/DrivetrainAi/drive/actions/workflows/production-v3-liquibase-restart.yml/dispatches"
    _headers = {'Authorization': f'token {git_token}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    _data = '{"ref": "main-v3"}'
    
    r = requests.post(action_url, headers=_headers, data=_data)
    app.logger.info(r.status_code)
    
    return r.status_code


def tenant_exists(tenant_id):
    if int(tenant_id):
        query = f"select * from public.tenants where schema = {tenant_id}"
        app.logger.info(query)
        qry_result = execute_v3_db_query(query)
        
        if len(qry_result) == 1:
            return True
        
        return False
    else:
        app.logger.error("tenant id is invalid")
        return {'status': 0, 'message': 'tenant id is invalid'}
    

def sandbox_exists(tenant_id):
    if int(tenant_id):
        query = f"select * from public.tenants where schema = 100{tenant_id}"
        app.logger.info(query)
        qry_result = execute_v3_db_query(query)
        
        if len(qry_result) == 1:
            return True
        
        return False
    else:
        app.logger.error("tenant id is invalid")
        return {'status': 0, 'message': 'tenant id is invalid'}
    

def add_okta_trusted_origin(tenant_id):
    okta_domain_url = "auth.drivetrain.ai"
    url = f"https://{okta_domain_url}/api/v1/trustedOrigins"

    payload = {
    "name": f"T-{tenant_id}",
    "origin": f"https://{tenant_id}.drivetrain.ai",
    "scopes": [
        {
        "type": "CORS"
        },
        {
        "type": "REDIRECT"
        }
    ]
    }

    headers = {
    "Content-Type": "application/json",
    "Authorization": f"SSWS {okta_api_key}"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    app.logger.info(f"Okta trusted origin response: {response.status_code}")


def add_sandbox_entry(tenant_id):
    result = {}
    result_list = []

    if int(tenant_id):

        # Getting the subdomain of the tenant
        subd_qry = f"select subdomain from public.tenants where schema = {tenant_id}"
        subd_qry_result = execute_v3_db_query(subd_qry)
        
        if len(subd_qry_result) == 1:
            subdomain_txt = f"{subd_qry_result[0]['subdomain']}-sandbox"

        else:
            subdomain_txt = f"100{tenant_id}"

        # Adding sandbox tenant id
        query = f"insert into public.tenants(subdomain, schema) values ('{subdomain_txt}', 100{tenant_id}) RETURNING *;"
        app.logger.info(query)
        qry_result = execute_v3_db_query(query, update=True)
        
        result['id'] = qry_result['id']
        result['subdomain'] = qry_result['subdomain']
        result['schema'] = qry_result['schema']
        result['created_at'] = qry_result['created_at'].strftime("%Y-%m-%d %H:%M:%S")
        result_list.append(result.copy())
        app.logger.info(f"Sandbox tenant added: {result_list}")

        # Add sandbox tenant to okta trusted origin
        add_okta_trusted_origin(str(result_list[0]['schema']))
        add_okta_trusted_origin(str(result_list[0]['subdomain']))

        # Run liquibase restart
        run_liquibase_restart()
        
        return result_list
    else:
        app.logger.error("tenant id is invalid")
        return {'status': 0, 'message': 'tenant id is invalid'}
