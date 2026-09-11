from dtapp.main import *
from dtapp.services.email_service import send_email_query_mail, send_email_query_html_mail
from dtapp.services.database_service import execute_db_query


def create_email_query(tenant, source_app, subject, big_query, email_recipients, email_content, cron):
    response = tbl_data_alerts_client.put_item(
        Item={
                "tenant": tenant,
                "source_app": source_app,
                "subject": subject,
                "big_query": big_query,
                "email_recipients": email_recipients,
                "email_content": email_content,
                "cron": cron,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    )
    scheduler.add_job(check_email_query, cron, args=[big_query, subject, source_app, email_recipients, email_content], job_id='emailQ_' + str(subject))

    return response


def get_all_email_query():
    _query_result = tbl_data_alerts_client.scan()
    return _query_result


def update_email_query(tenant, source_app, subject, big_query, email_recipients, email_content, cron):
    response = tbl_data_alerts_client.update_item(
        Key = {'tenant': tenant, 'subject': subject},
        ExpressionAttributeValues= { 
            ":s": source_app,
            ":b": big_query,
            ":e": email_recipients,
            ":c": email_content,
            ":r": cron
        },
        UpdateExpression="set source_app = :s, big_query = :b, email_recipients = :e, email_content = :c, cron = :r",
        ReturnValues="UPDATED_NEW",
    )

    return response


def delete_email_query(tenant, subject):
    response = tbl_data_alerts_client.delete_item(
        Key={
                "tenant": tenant,
                "subject": subject
            }
    )
    scheduler.remove_job('emailQ_' + str(subject))

    return response
    

def get_tenant_email_query(tenant_id):
    response = tbl_data_alerts_client.query(KeyConditionExpression=Key('tenant').eq(tenant_id))
    return response


def trigger_email_query(tenant_id):
    response = tbl_data_alerts_client.query(KeyConditionExpression=Key('tenant').eq(str(tenant_id)))
    for x in response['Items']:
        check_email_query(x['big_query'], x['subject'], x['source_app'], x['email_recipients'], x['email_content'])
    return response


def check_email_query(query, subject, source_app, email_recipients, email_content):
    app.logger.info("Starting check_null_data for query ".format(str(query)))
    csv_file = "/tmp/" + str(datetime.today().strftime("%Y-%m-%d-%H-%M-%S-%f")) + ".csv"
    styled_df = None

    app.logger.info(f"{subject} - {query}")
    dataframe = (
        gcp_client.query(query).result().to_dataframe()
    )

    if not dataframe.empty:
        app.logger.info(f"Query has output for {subject} to {csv_file}")
        dataframe.to_csv(csv_file)

        if "{}" in email_content:
            # styled_df = dataframe.style.set_properties(**{'text-align': 'center'})
            styled_df = build_table(dataframe, 'blue_dark', padding='5px', text_align='center')

        for recipients in email_recipients:
            if "{}" in email_content:
                app.logger.info(f"Sending html email for {recipients} - {subject}")
                send_email_query_html_mail(subject, recipients, str(email_content), styled_df)
            else:
                app.logger.info(f"Sending email for {recipients} - {subject}")
                send_email_query_mail(source_app, subject, recipients, str(email_content), csv_file)

        os.remove(csv_file)


def check_unused_primary_tables(tenant_id):
    result_list = []
    query = f"select distinct filters ->> 'tableName' as table_id from \"{tenant_id}\".queries where status=1;"
    app.logger.info(f"Getting used table from pg - {query}")
    qry_result = execute_db_query(query)

    for v in qry_result:
        if not "_version" in v['table_id']:
            result_list.append(v['table_id'])
    
    return result_list
