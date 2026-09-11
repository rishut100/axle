from dtapp.views.wraps import *
from sendgrid import SendGridAPIClient, To, From, Cc, Bcc
from sendgrid.helpers.mail import (Mail, Attachment, FileContent, FileName, FileType, Disposition)

api_key = os.environ.get('SENDGRID_API_KEY')


def get_sendgrid_client():
    sg = SendGridAPIClient(api_key)
    if app.config.get('AWS_REGION') == 'eu-central-1':
        sg.set_sendgrid_data_residency('eu')
    return sg


def send_email_query_mail(source_app, subject, email_recipients, email_content, file_name):
    app.logger.info("Sending query email to the recipients %s", email_recipients)
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=str(email_recipients),
            subject=subject,
            plain_text_content=email_content)

    with open(file_name, 'rb') as f:
        data = f.read()
        f.close()
    encoded_file = base64.b64encode(data).decode()

    attached_file = Attachment(
        FileContent(encoded_file),
        FileName(source_app + '.csv'),
    )
    message.attachment = attached_file

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(f"Email sent - {subject} - {response.status_code}")
    except Exception as e:
        app.logger.error(e.message)


def send_email_query_html_mail(subject, email_recipients, email_content, file_name):
    app.logger.info("Sending query email to the recipients %s", email_recipients)
    message = Mail(
            from_email=From("drivetrainteam@drivetrain.ai", "Drivetrain Alerts"),
            to_emails=[To(email_recipients)],
            subject=subject,
            html_content=email_content.format(file_name))

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_dtml_backup_email(subject, content):
    app.logger.info("Sending dtml backup email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To('sre-team@drivetrain.ai')],
            subject="Daily DTML backup results - " + subject,
            plain_text_content="Here are the list of tenants for which model dtml backed up: \n\n" + content)

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_scheduled_emails(email_recipients, subject, email_content):
    app.logger.info("Sending scheduled email to the recipients %s", email_recipients)
    message = Mail(
            from_email=From("alerts@drivetrain.ai", "Drivetrain Alerts"),
            to_emails=[To(email_recipients)],
            subject=subject,
            html_content=email_content)

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_db_backup_emails(email_recipients, source_tenant_id, destination_tenant_id):
    app.logger.info("Sending db backup result email to the recipients %s", email_recipients)
    message = Mail(
            from_email=From("alerts@drivetrain.ai", "Drivetrain Alerts"),
            to_emails=[To(email_recipients)],
            subject="DB backup and restore mail",
            html_content=f"DB backup done from {source_tenant_id} to {destination_tenant_id}")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_gandalf_test_email(email_recipients, tenant_id, file_name, file_content):
    app.logger.info("Sending gandalf test result mail to the recipients %s", email_recipients)
    message = Mail(
            from_email=From("alerts@drivetrain.ai", "Drivetrain Alerts"),
            to_emails=[To(email_recipients)],
            subject=f"Gandalf test results for {tenant_id}",
            plain_text_content=f"Here is your result for gandalf test for tenant {tenant_id}. Attached the result file in this email.")

    # with open(file_name, 'rb') as f:
    #     data = f.read()
    #     f.close()
    encoded_file = base64.b64encode(file_content).decode()

    attached_file = Attachment(
        FileContent(encoded_file),
        FileName("gandalf_result.txt"),
    )
    message.attachment = attached_file

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_gandalf_prepare_db_mail(email_recipients, tenant_id):
    app.logger.info("Sending gandalf test result mail to the recipients %s", email_recipients)
    message1 = Mail(
            from_email='alerts@drivetrain.ai',
            to_emails=[To(email_recipients)],
            subject="Gandalf db prepare mail",
            plain_text_content=f"Backup from prod tenant {tenant_id} has been restored to 10{tenant_id} in prod verify rds")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message1)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e)


def test_email_send():
    app.logger.info("Sending test email to the recipients")
    message1 = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails='kabilan@drivetrain.ai',
            subject="Email test",
            plain_text_content="Please ignore")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message1)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e)


def okta_new_user_alert_mail(username, user_name, user_tenant_id):
    app.logger.info("Sending okta user create alert email to the recipients")
    message1 = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails='okta-alerts@drivetrain.ai',
            subject="New User creation in Okta",
            plain_text_content=f"New user created in Okta {username} and full name {user_name} for tenant {user_tenant_id}. Please reset the password.")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message1)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e)


def okta_password_reset_alert_mail(username, user_tenant_id):
    app.logger.info("Sending okta user password reset alert email to the recipients")
    message1 = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails='okta-alerts@drivetrain.ai',
            subject="User password reset in Okta",
            plain_text_content=f"Password reset requested by {username} for {user_tenant_id} in Okta.")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message1)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e)


def okta_delete_user_alert_mail(username):
    app.logger.info("Sending okta user delete alert email to the recipients")
    message1 = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails='okta-alerts@drivetrain.ai',
            subject="User deleted in Okta",
            plain_text_content=f"User {username} is deleted.")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message1)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e)


def send_tenant_access_approval_mail(id, tenant_id, user_email, validity, reason):
    app.logger.info("Sending tenant access approval email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To('tenant-flag-approvers@drivetrain.ai')],
            subject=f"Action Required: Tenant Access Approval - {tenant_id} for {user_email}",
            plain_text_content=f"Please use the link below to approve the request: \n\nEmail: {user_email} \nTenant id: {tenant_id} \nValidity: {validity} \nReason: {reason} \n\n https://drivetrain.retool.com/app/tenant_access#id={id}")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_tenant_access_approval_mail_v3(id, tenant_id, user_email, validity, reason):
    app.logger.info("Sending tenant access approval email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To('tenant-flag-approvers@drivetrain.ai')],
            subject=f"Action Required: Tenant Access Approval - {tenant_id} for {user_email}",
            plain_text_content=f"Please use the link below to approve the request: \n\nEmail: {user_email} \nTenant id: {tenant_id} \nValidity: {validity} \nReason: {reason} \n\n https://drivetrain.retool.com/app/tenant_access_v3#id={id}")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_tenant_access_approval_confirmation_mail(tenant_id, user_email, reason):
    app.logger.info("Sending tenant access approval confirmation email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To(user_email)],
            subject=f"Tenant Access {reason} - {tenant_id} for {user_email}",
            plain_text_content=f"Please reach out to Alok/Tark/Paaras/Saurav/Jason for any queries")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_bigquery_access_approval_mail(id, tenant_id, user_email, validity, reason):
    app.logger.info("Sending Big Query access approval email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To('tenant-flag-approvers@drivetrain.ai')],
            subject=f"Action Required: Big Query Access Approval - {tenant_id} for {user_email}",
            plain_text_content=f"Please use the link below to approve the request: \n\nEmail: {user_email} \nTenant id: {tenant_id} \nValidity: {validity} \nReason: {reason} \n\n https://drivetrain.retool.com/app/bigquery_access#id={id}")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_bigquery_access_approval_confirmation_mail(tenant_id, user_email, reason):
    app.logger.info("Sending Bigquery access approval confirmation email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To(user_email)],
            subject=f"Bigquery Access Approved reason: {reason} - {tenant_id} for {user_email}",
            plain_text_content=f"Please reach out to Alok/Tark/Kabi/Sateesh for any queries")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_bigquery_access_rejection_confirmation_mail(tenant_id, user_email, reason):
    app.logger.info("Sending Bigquery access Rejection confirmation email to the recipients")
    message = Mail(
            from_email='drivetrainteam@drivetrain.ai',
            to_emails=[To(user_email)],
            subject=f"Bigquery Access Rejected reason: {reason} - {tenant_id} for {user_email}",
            plain_text_content=f"Please reach out to Alok/Tark/Kabi/Sateesh for any queries")

    try:
        sg = get_sendgrid_client()
        response = sg.send(message)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(e.message)


def send_git_repo_creation_email(repo_name, initiated_by, success, message):
    app.logger.info("Sending git repo creation notification to sre-team")
    status = "Success" if success else "Failure"
    repo_url = f"https://github.com/DrivetrainAi/{repo_name}"
    body = (
        f"Git Repository Creation - {status}\n\n"
        f"Repository: {repo_name}\n"
        f"Initiated by: {initiated_by}\n"
        f"Status: {status}\n"
        f"Details: {message}\n"
    )
    if success:
        body += f"Repository URL: {repo_url}\n"

    mail = Mail(
        from_email='drivetrainteam@drivetrain.ai',
        to_emails=[To('sre-team@drivetrain.ai')],
        subject=f"Git Repo Creation {status}: {repo_name}",
        plain_text_content=body,
    )

    try:
        sg = get_sendgrid_client()
        response = sg.send(mail)
        app.logger.info(response.status_code)
    except Exception as e:
        app.logger.error(str(e))
