from dtapp.views.email_query import *
from dtapp.views.scheduled_emails import *
from dtapp.views.git_rules import *
from dtapp.views.infra import *
from dtapp.views.bigquery_access import revoke_expired_access
from dtapp.main import scheduler


def schedule_email_query():
    # Scheduler for email query
    data = get_all_email_query()
    app.logger.info("getting all bq email query for scheduling")
    for x in data['Items']:
        try:
            app.logger.info("adding schedule job for %s email query", x['subject'])
            scheduler.add_job(
                check_email_query,
                x['cron'],
                args=[
                    x['big_query'],
                    x['subject'],
                    x['source_app'],
                    x['email_recipients'],
                    x['email_content'],
                ],
                job_id='emailQ_' + str(x['subject'])
            )
        except Exception as e:
            app.logger.error(e)
            continue


def schedule_scheduled_emails():
    data = get_all_scheduled_emails()
    app.logger.info("getting all scheduled emails for scheduling")
    app.logger.info(data)
    for x in data['Items']:
        try:
            app.logger.info("adding schedule job for %s scheduled email", x['subject'])
            scheduler.add_job(
                run_scheduled_emails,
                x['cron'],
                args=[
                    x['email_recipients'],
                    x['subject'],
                    x['email_content'],
                    x['params'],
                ],
                job_id='schd_email_' + str(x['subject'])
            )
        except Exception as e:
            app.logger.error(e)
            continue


def schedule_git_repo_check():
    try:
        app.logger.info("adding schedule job for git repo check")
        scheduler.add_job(git_repo_status_check, '0 2 */2 * *', job_id='git_repo_check')
    except Exception as e:
        app.logger.error(e)


def schedule_revoke_expired_access():
    try:
        app.logger.info("adding schedule job for revoking big query access")
        scheduler.add_job(revoke_expired_access, '0 2 * * *', job_id='bigquery_revoke_expired')
    except Exception as e:
        app.logger.error(e)


def get_scheduled_jobs():
    return scheduler.get_jobs()


def schedule_dtml_backup_v2():
    try:
        app.logger.info("adding schedule job for v2 dtml backup")
        scheduler.add_job(run_dtml_backup_v2, '05 3 * * *', job_id='dtml_backup_v2')
    except Exception as e:
        app.logger.error(e)


def schedule_dtml_backup_v3():
    try:
        app.logger.info("adding schedule job for v3 dtml backup")
        scheduler.add_job(run_dtml_backup_v3, '05 4 * * *', job_id='dtml_backup_v3')
    except Exception as e:
        app.logger.error(e)