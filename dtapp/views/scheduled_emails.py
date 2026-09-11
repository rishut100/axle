from dtapp.main import *
from dtapp.services.email_service import send_scheduled_emails


def get_extracted_params(content):
    sub1 = "{{"
    sub2 = "}}"

    s1 = str(re.escape(sub1)) 
    s2 = str(re.escape(sub2))
    return re.findall(s1+"(.*)"+s2,content)


def generate_content(content, cust_value, param):
    r_type = param[cust_value]['type']
    r_value = param[cust_value]['value']
    r_format = param[cust_value]['format']

    if r_type == "date":
        replaced_content = get_scheduled_email_date_content(content, cust_value, r_format)
    
    if r_type == "past_date":
        replaced_content = get_scheduled_email_past_date_content(content, cust_value, r_format, r_value)
    
    return replaced_content


def get_scheduled_email_date_content(content, param, r_format):
    _date = datetime.today().strftime(r_format)
    return content.replace("{{"+ param +"}}", _date)


def get_scheduled_email_past_date_content(content, param, r_format, r_value):
    _date = (datetime.today() - relativedelta(months=+int(r_value))).strftime(r_format)
    return content.replace("{{"+ param +"}}", _date)


def create_scheduled_emails(tenant, source_app, email_recipients, subject, email_content, params, cron):
    response = tbl_scheduled_email_client.put_item(
        Item={
                "tenant": tenant,
                "source_app": source_app,
                "subject": subject,
                "email_recipients": email_recipients,
                "email_content": email_content,
                "params": params,
                "cron": cron,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    )

    return response


def get_all_scheduled_emails():
    _query_result = tbl_scheduled_email_client.scan()
    return _query_result


def run_scheduled_emails(email_recipients, subject, email_content, params):

    modified_email_content = email_content
    
    if "{{" in email_content:
        params_list = get_extracted_params(email_content)
        for _cust_params in params_list:
            modified_email_content = generate_content(modified_email_content, _cust_params, params)
            
    app.logger.info(modified_email_content)

    for recipients in email_recipients:
        send_scheduled_emails(recipients, subject, modified_email_content)
