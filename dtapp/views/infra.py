from dtapp.main import *
from dtapp.services.email_service import send_dtml_backup_email, send_db_backup_emails
from dtapp.services.database_service import *
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud.bigquery.job import CopyJobConfig

job_config = CopyJobConfig(write_disposition="WRITE_TRUNCATE")
global_key_name = app.config['GLOBAL_KEY_SECRET_NAME']


def execute_full_db_backup(source_id, file_name):
    ret_backup = 1
    db_name, db_host, _ = get_v3_db_config()
    db_user = app.config['DRIVE_USER']
    db_password = db_pass

    ret_backup = subprocess.call(
        f"export PGPASSWORD={db_password} && pg_dump -h {db_host} -d {db_name} -U {db_user} -n {source_id} > /tmp/{file_name}",
        shell=True)

    if ret_backup:
        app.logger.info("Prod full tenant backup dump is failed")
        return False

    return True


def copy_to_s3(tenant_id, file_name):
    ret_copy = 1
    ret_copy = subprocess.call(f"aws s3 cp /tmp/{file_name} s3://dt-devops/backup/prod/{tenant_id}/{file_name}", shell=True)

    if ret_copy:
        app.logger.info("S3 copy is failed")
        return False

    return True


def run_db_backup(tenant_id): 
    current_date = datetime.today().strftime("%Y_%m_%d")
    file_name = f"{tenant_id}_{str(current_date)}.sql"
    app.logger.info(file_name)

    _result = execute_full_db_backup(tenant_id, file_name)
    if _result:
        copy_to_s3(tenant_id, file_name)
    subprocess.call(f"rm -rf /tmp/{file_name}", shell=True)

    if _result:
        return {'message': 'success', 'file_name': file_name}
    else:
        return {'message': 'failed', 'file_name': file_name}


def copy_table_to_preprod(table, source, destination, source_id, destination_id):
    _source = source.split('_')[0]
    _destination = destination.split('_')[0]
    replaced_table_id = table.table_id.replace(f"tenant{_source}", f"tenant{_destination}")
    app.logger.info(f"copying table from dataset - {source_id} - {destination_id}: {replaced_table_id}")
    gcp_client.copy_table(f"{source_id}.{table.table_id}", f"{destination_id}.{replaced_table_id}")


def copy_dataset_to_preprod(source, destination):
    try:
        source_dataset_id = f'dtx-springs.tenant{source}'
        destination_dataset_id = f'drivetrain-preprod.tenant{destination}'

        gcp_client.delete_dataset(destination_dataset_id, not_found_ok=True, delete_contents=True)
        gcp_client.create_dataset(destination_dataset_id, exists_ok=True)

        tables = list(gcp_client.list_tables(source_dataset_id))
        app.logger.info(f"Total tables to copy: {len(tables)}")
        
        max_workers = max(1, min(len(tables), 12))
        copied: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_table = {
                executor.submit(copy_table_to_preprod, table, source, destination, source_dataset_id, destination_dataset_id): table
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
        app.logger.error(f"Exception - {e}")
        raise Exception (f"Tenant dataset copy failed for {source} to {destination}")


def execute_db_backup(source_id, file_name):
    ret_backup = 1
    db_name, db_host, _ = get_v3_db_config()
    db_user = app.config['DRIVE_USER']
    db_password = db_pass

    ret_backup = subprocess.call(
        f"export PGPASSWORD={db_password} && pg_dump -h {db_host} -d {db_name} -U {db_user} -n {source_id} --exclude-table-data {source_id}.fivetran_groups --exclude-table-data {source_id}.users --exclude-table-data {source_id}.user_roles > /tmp/{file_name}",
        shell=True)

    if ret_backup:
        app.logger.info("Prod tenant backup dump is failed")
        return False

    return True


def execute_preprod_db_restore(source_id, destination_id, file_name):
    ret_restore = 1
    db_name = "product_preprod_v3"
    db_host = "rds-preprod.example.internal"
    db_user = app.config['DRIVE_USER']
    db_password = db_pass

    sed_cmd = f"sed -i -r 's/\"{source_id}\"/\"{destination_id}\"/g' /tmp/{file_name} && sed -i -r 's/\"tenant{source_id}\"/\"tenant{destination_id}\"/g' /tmp/{file_name}"
    app.logger.info(sed_cmd)
    ret_sed = subprocess.Popen(sed_cmd, shell=True)
    ret_sed.wait()

    if ret_sed.returncode == 0:
        # Drop the schema and restore from dump
        rs_cmd = f"export PGPASSWORD={db_password} && psql -U {db_user} -h {db_host} -p 5432 -d {db_name} -t -A -F',' -c 'DROP SCHEMA if exists \"{destination_id}\" CASCADE;' && psql -h {db_host} -d {db_name} -U {db_user} < /tmp/{file_name} --quiet && rm -rf /tmp/{file_name}"
        ret_restore = subprocess.Popen(rs_cmd, shell=True)
        ret_restore.wait()

    if ret_restore.returncode != 0:
        app.logger.info("Preprod tenant restore failed")
        return False

    return True


def run_db_backup_restore_preprod(source_tenant_id, destination_tenant_id):
    current_date = datetime.today().strftime("%Y_%m_%d")
    file_name = f"{source_tenant_id}_{str(current_date)}.sql" 
    app.logger.info(file_name)
    
    _backup = execute_db_backup(source_tenant_id, file_name)
    if not _backup:
        subprocess.call(f"rm -rf {file_name}", shell=True)
        return {'message': 'failed', 'file_name': file_name}
    
    _restore = execute_preprod_db_restore(source_tenant_id, destination_tenant_id, file_name)
    copy_dataset_to_preprod(source_tenant_id, destination_tenant_id)
    copy_dataset_to_preprod(f"{source_tenant_id}_src", f"{destination_tenant_id}_src")
    
    if _backup and _restore:
        return {'message': 'success', 'file_name': file_name}
    else:
        return {'message': 'failed', 'file_name': file_name}


def run_dtml_backup_v2():
    mail_content = ""
    mail_subject = ""
    content_list = []
    today = datetime.today().strftime("%Y%m%d-%H%M")

    # Get global api key
    get_secret_value_response = secrets_client.get_secret_value(SecretId=global_key_name)
    key = json.loads(get_secret_value_response["SecretString"])["global.drive.auth.key"]

    # Getting the list of v2 tenants
    result = execute_db_query("select schema as tenant_id from public.tenants where status=1 order by tenant_id;")
    for _id in result:
        tenant_id = _id['tenant_id']
        if _id['tenant_id']:

            # Model dtml backup
            url = f"https://{tenant_id}.example.com/drive/api/v1/public/dtml/model"
            headers = {
                "Content-Type": "application/json",
                "apikey": key
            }
            api_response = requests.get(url, headers=headers)

            if api_response.status_code == 500:
                continue

            dtml_file_name = f'{int(tenant_id):03d}'
            model_key = f'backup/dtmls/v2/{today}/{dtml_file_name}-model-dtml.json'
            app.logger.info(model_key)
            
            response = s3_client.put_object(Bucket='dt-devops',
                                    Body=api_response.content,
                                    Key=model_key)['ResponseMetadata']['HTTPStatusCode']
            
            if (response == 200):
                content_list.append(f"{dtml_file_name} - dt-devops/{model_key} - ok")
            else:
                content_list.append(f"{dtml_file_name} - dt-devops/{model_key} - failed")
            
    mail_content = '\n'.join(sorted(content_list, reverse=True))
    if "failed" in mail_content:
        mail_subject = "FAILED"
    else:
        mail_subject = "SUCCESS"

    send_dtml_backup_email(mail_subject, mail_content)


def run_dtml_backup_v3():
    mail_content = ""
    mail_subject = ""
    content_list = []
    today = datetime.today().strftime("%Y%m%d-%H%M")

    # Get global api key
    get_secret_value_response = secrets_client.get_secret_value(SecretId=global_key_name)
    key = json.loads(get_secret_value_response["SecretString"])["global.drive.auth.key"]

    # Getting the list of v2 tenants
    result = execute_v3_db_query("select schema as tenant_id from public.tenants where status=1 order by tenant_id;")
    for _id in result:
        tenant_id = _id['tenant_id']
        if _id['tenant_id']:

            # Model dtml backup
            url = f"https://{tenant_id}.example.com/drive/api/v1/public/dtml/model"
            headers = {
                "Content-Type": "application/json",
                "apikey": key
            }
            api_response = requests.get(url, headers=headers)

            if api_response.status_code == 500:
                continue

            dtml_file_name = f'{int(tenant_id):03d}'
            model_key = f'backup/dtmls/v3/{today}/{dtml_file_name}-model-dtml.json'
            app.logger.info(model_key)
            
            response = s3_client.put_object(Bucket='dt-devops',
                                    Body=api_response.content,
                                    Key=model_key)['ResponseMetadata']['HTTPStatusCode']
            
            if (response == 200):
                content_list.append(f"{dtml_file_name} - dt-devops/{model_key} - ok")
            else:
                content_list.append(f"{dtml_file_name} - dt-devops/{model_key} - failed")
            
    mail_content = '\n'.join(sorted(content_list, reverse=True))
    if "failed" in mail_content:
        mail_subject = "FAILED"
    else:
        mail_subject = "SUCCESS"

    send_dtml_backup_email(mail_subject, mail_content)