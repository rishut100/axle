from dtapp.main import *
from dtapp.services.database_service import *
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud.bigquery.job import CopyJobConfig

okta_api_key = os.environ.get('OKTA_API_TOKEN')
gcp_project = "dtx-springs"
job_config = CopyJobConfig(write_disposition="WRITE_TRUNCATE")


def get_active_tenant_list_v3():
    result = {}
    result_list = []

    query = f"select * from public.tenants where (schema >= 400 and schema < 900) and status=1 and subdomain ~ '[A-Za-z]'"
    qry_result = execute_v3_db_query(query)

    for v in qry_result:
        result['tenant_id'] = v['schema']
        result['subdomain'] = v['subdomain']
        result_list.append(result.copy())
    
    app.logger.info(f"Active tenant list: {result_list}")
    
    return result_list


def get_tenant_domain(id):
    result = {}

    query = f"select subdomain from public.tenants where schema = {id} and status=1"
    qry_result_v3 = execute_v3_db_query(query)
    qry_result_v2 = execute_db_query(query)

    if len(qry_result_v3) > 0:
        result['subdomain'] = qry_result_v3[0]['subdomain']
    elif len(qry_result_v2) > 0:
        result['subdomain'] = qry_result_v2[0]['subdomain']
    
    return result


def get_all_tenant_list_v3():
    result = {}
    result_list = []

    query = f"select * from public.tenants"
    qry_result = execute_v3_db_query(query)

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
    drop_schema_query = f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
    drop_v3_db_query(drop_schema_query)


def build_query_to_fetch_records_count(tenant_id):
    return 'SELECT COUNT(*) AS row_count FROM "'+tenant_id+'".metrics ' \
           'UNION ALL ' \
           'SELECT COUNT(*) AS row_count FROM "'+tenant_id+'".connection_instances;'


def is_tenant_reset(tenant_id, bq_schema):
    temp = False
    query = build_query_to_fetch_records_count(str(tenant_id))
    result = execute_v3_db_query(query)
    filtered_result = [row for row in result if row['row_count'] > 0]
    
    if filtered_result is not None and len(filtered_result) > 0:
        temp = False
    else:
        temp = True

    create_dataset(bq_schema)  ## create dataset if it's not already present.
    create_dataset(f"{bq_schema}_src")
    try:
        if temp:
            tables = list(gcp_client.list_tables(f"{gcp_project}.{bq_schema}"))
            if tables:
                temp = False
            else:
                temp = True

        if temp:
            src_tables = list(gcp_client.list_tables(f"{gcp_project}.{bq_schema}_src"))
            if src_tables:
                temp = False
            else:
                temp = True
    except Exception as e:
        app.logger.info("Dataset not found")
        temp = True
    return temp


def backup_restore_schema(source_id, destination_id, file_name):
    ret_backup = 1
    ret_restore = 1
    db_name = app.config['DRIVE_V3_DB']
    db_host = app.config['DRIVE_V3_HOST']
    db_port = app.config['DRIVE_V3_PORT']
    db_user = app.config['DRIVE_USER']
    db_password = db_pass

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
            fsm_result = execute_v3_db_query(fsm_query)
            app.logger.info(fsm_result[0]['fiscal_start_month'])

            fsm_upd_query = f"update public.tenants set fiscal_start_month = '{fsm_result[0]['fiscal_start_month']}' where schema = {destination_id} RETURNING *;"
            app.logger.info(fsm_upd_query)
            execute_v3_db_query(fsm_upd_query, update=True)

        else:
            app.logger.info("db restore: failed")
            return False
    else:
        app.logger.info("sed cmd: failed")
        return False

    return True


def copy_primary_dataset(source_id, destination_id, source_bq_schema, destination_bq_schema):
    try:
        tables = list(gcp_client.list_tables(f"{gcp_project}.{source_bq_schema}"))
        app.logger.info(f"Total tables to copy: {len(tables)}")
        create_dataset(f"{destination_bq_schema}")
        
        max_workers = max(1, min(len(tables), 12))
        copied: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_table = {
                executor.submit(copy_table, table, source_id, destination_id, f"{destination_bq_schema}"): table
                for table in tables
            }
            for future in as_completed(future_to_table):
                table = future_to_table[future]
                try:
                    if future.result():
                        copied.append(table.table_id)
                except Exception as e:
                    app.logger.error(f"schema copy: Table {table.table_id} generated an exception: {e}")
                    raise
        return True
        
    except Exception as e:
        app.logger.error(f"Copy primary dataset for tenant {source_id} failed with error {e}")
        return False


def copy_src_dataset(source_id, destination_id, source_bq_schema, destination_bq_schema):
    try:
        tables = list(gcp_client.list_tables(f"{gcp_project}.{source_bq_schema}_src"))
        app.logger.info(f"Total tables to copy: {len(tables)}")
        create_dataset(f"{destination_bq_schema}_src")

        max_workers = max(1, min(len(tables), 12))
        copied: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_table = {
                executor.submit(copy_table, table, source_id, destination_id, f"{destination_bq_schema}_src"): table
                for table in tables
            }
            for future in as_completed(future_to_table):
                table = future_to_table[future]
                try:
                    if future.result():
                        copied.append(table.table_id)
                except Exception as e:
                    app.logger.error(f"schema copy: Table {table.table_id} generated an exception: {e}")
                    raise
        return True
    except Exception as e:
        app.logger.error(f"Dataset not found in source tenant {source_id} primary dataset, {e}")
        return False


def copy_table(table, source_id, destination_id, destination_bq_schema):
    source_table_id = f'{table.project}.{table.dataset_id}.{table.table_id}' 
    temp_destination_id = f'{table.table_id}'.replace(f'tenant{source_id}', f'tenant{destination_id}')
    destination_table_id = f'{table.project}.{destination_bq_schema}.{temp_destination_id}'
    app.logger.info(f"copying table from dataset - {source_table_id} - {destination_table_id}")

    job = gcp_client.copy_table(source_table_id, destination_table_id, job_config=job_config)
    job.result()


def create_dataset(dataset_name):
    # Create dataset if not exists
    app.logger.info(f"Creating dataset {dataset_name} in Bigquery")
    gcp_client.create_dataset(f"{gcp_project}.{dataset_name}", exists_ok = True)
    app.logger.info(f"Created dataset {dataset_name} successfully")
