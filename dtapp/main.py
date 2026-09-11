from functools import wraps

import random, requests, hashlib, ast, csv, base64, subprocess, time, json, re, jwt, os, socket
from io import StringIO
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from pretty_html_table import build_table
from time import strftime
import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from threading import Thread

from logging.config import dictConfig
import logging
import atexit

log_dir = os.environ.get("LOG_DIR", "/var/log/axle")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, os.environ.get('LOG_FILE', 'axle.log'))
hostname = socket.gethostname()

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


class SafeFormatter(logging.Formatter):
    def format(self, record):
        if "client_ip" not in record.__dict__:
            record.__dict__["client_ip"] = ''
        if "host" not in record.__dict__:
            record.__dict__["host"] = hostname
        if "http_status" not in record.__dict__:
            record.__dict__["http_status"] = ''
        if "method" not in record.__dict__:
            record.__dict__["method"] = ''
        if "url" not in record.__dict__:
            record.__dict__["url"] = ''
        if "exception_trace" not in record.__dict__:
            record.__dict__["exception_trace"] = ''
        return super().format(record)


class JSONFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.now()
        s = ct.strftime('%Y-%m-%dT%H:%M:%S')
        s = f"{s}.{int(ct.microsecond/1000):03d}Z"
        return s

    def format(self, record):
        method = record.__dict__.get('method', '')
        http_status = record.__dict__.get('http_status', '')
        url = record.__dict__.get('url', '')
        client_ip = record.__dict__.get('client_ip', '')
        host = record.__dict__.get('host', hostname)
        exception_trace = record.__dict__.get('exception_trace', '')

        payload = {
            'datetimestamp': self.formatTime(record),
            'level': record.levelname,
            'method': method,
            'http_status': http_status,
            'request_url': url,
            'client_ip': client_ip,
            'host': host,
            'exception_trace': exception_trace,
            'message': record.getMessage()
        }

        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return json.dumps({k: str(v) for k, v in payload.items()}, ensure_ascii=False)

FORMAT = '%(asctime)s | %(levelname)s | %(method)s | %(http_status)s | %(url)s | %(client_ip)s | %(host)s | axle | %(message)s | %(exception_trace)s'

dictConfig({
    'version': 1,
    'formatters': {'default': {
        '()': SafeFormatter,
        'format': FORMAT,
    }, 'json': {
        '()': JSONFormatter
    }},
    'handlers': {
        'wsgi': {
        'class': 'logging.StreamHandler',
        'stream': 'ext://sys.stdout',
        'formatter': 'json'
    },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': log_file,
            'maxBytes': 10485760,
            'backupCount': 5,
            'formatter': 'default',
            'encoding': 'utf8'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['wsgi', 'file']
    }
})
app_env = os.environ.get('DTADMIN_ENV')

# Real employee roster redacted for the public repo — populate with your own.
leads_list = [
    "lead1@example.com",
    "lead2@example.com",
]

# --------------------------------------------
## Flask app initialisation
# --------------------------------------------
from flask import Flask
from flask import request, redirect, url_for, json, jsonify, Response
from flask.logging import default_handler
from flask_cors import CORS, cross_origin

app = Flask(__name__)
app.config.from_object('env.{0}'.format(app_env))


# --------------------------------------------
## Boto Initializers
# --------------------------------------------
from dtapp.models import *

region_name = app.config['AWS_REGION']
boto_session = boto3.session.Session()
ssm_client = boto3.client('ssm', region_name=region_name)
s3_client = boto3.client('s3', region_name=region_name)
dynamodb_client = boto3.resource('dynamodb', region_name=region_name)
secrets_client = boto_session.client(service_name='secretsmanager', region_name=region_name)

git_token = os.environ.get('GIT_TOKEN')
db_pass = os.environ.get('DB_PASS')


# --------------------------------------------
## Gcloud initializers
# --------------------------------------------
from google.cloud import bigquery
from google.oauth2 import service_account

OPT_PATH = os.environ['OPT_PATH']
bigquery_key_path = f'{OPT_PATH}/gcn-key.json'
credentials = service_account.Credentials.from_service_account_file(bigquery_key_path, scopes=[
    'https://www.googleapis.com/auth/cloud-platform'], )
gcp_project_id = credentials.project_id
gcp_client = bigquery.Client(credentials=credentials, project=gcp_project_id, )


# --------------------------------------------
## Scheduler config and initiation
# --------------------------------------------
from dtapp.cron_scheduler import CronScheduler
scheduler = CronScheduler()

# --------------------------------------------
## Model initializers
# --------------------------------------------
tbl_accounts_client, tbl_data_alerts_client, tbl_scheduled_email_client, tbl_bq_access_requests = execute_model()


# --------------------------------------------
## Kickstart bootstrap — blueprints registered here; SQS client owned by garage/kickstart/queue.py
# --------------------------------------------
if (app_env or '').lower() != 'prod-eu':
    from dtapp.garage.kickstart.repositories.kickoff_repo import init_schema as kickstart_init_schema
    kickstart_init_schema()
    from dtapp.garage.kickstart.queue import start_provisioning_consumer_thread
    start_provisioning_consumer_thread()  # consumes Axle's own provisioning queue ({koid}); makes the sync Drive REST create
    from dtapp.garage.kickstart.api.kickoff_api import kickstart_bp
    app.register_blueprint(kickstart_bp)
    from dtapp.garage.assets.api import assets_bp
    app.register_blueprint(assets_bp)
    from dtapp.garage.kickstart.services.consultant_service import sync_consultants
    scheduler.add_job(sync_consultants, '0 14 * * *', job_id='kickstart_consultant_sync')  # 2 PM IST (prod must run TZ=Asia/Kolkata)
    from dtapp.garage.core import constants as _kc
    from dtapp.garage.kickstart.reminders import run_intake_reminders, run_consultant_reminders
    scheduler.add_job(run_intake_reminders, _kc.INTAKE_REMINDER_CRON, job_id=_kc.INTAKE_REMINDER_JOB_ID)
    scheduler.add_job(run_consultant_reminders, _kc.CONSULTANT_REMINDER_CRON, job_id=_kc.CONSULTANT_REMINDER_JOB_ID)

    # Access automation (ENG-90754) — separate module, separate SQS queue/consumer (see
    # access/CLAUDE.md), same non-prod-eu guard as kickstart above.
    from dtapp.access.repositories import access_event_repo as _access_event_repo
    from dtapp.access.matrix import matrix_repo as _access_matrix_repo
    _access_event_repo.init_schema()
    _access_matrix_repo.init_schema()
    from dtapp.access.queue import start_access_consumer_thread
    start_access_consumer_thread()
    from dtapp.access.api.access_api import access_bp
    app.register_blueprint(access_bp)
    from dtapp.access.services.verify_service import run_revoke_verification, run_grant_verification
    from dtapp.access import constants as _ac
    scheduler.add_job(run_revoke_verification, _ac.REVOKE_VERIFY_CRON, job_id=_ac.REVOKE_VERIFY_JOB_ID)
    scheduler.add_job(run_grant_verification, _ac.GRANT_VERIFY_CRON, job_id=_ac.GRANT_VERIFY_JOB_ID)


# --------------------------------------------
## Scheduler jobs
# --------------------------------------------
from dtapp.views import scheduler as scheduler_views
# These are prod-only operational jobs (scheduled emails, access-revoke, DTML backups) that hit
# AWS/DynamoDB at boot — run them only in prod, so non-prod/local boots don't fire them.
if (app_env or '').lower() == 'prod':
    scheduler_views.schedule_email_query()
    scheduler_views.schedule_scheduled_emails()
    scheduler_views.schedule_git_repo_check()
    scheduler_views.schedule_revoke_expired_access()
    scheduler_views.schedule_dtml_backup_v2()
    scheduler_views.schedule_dtml_backup_v3()


# --------------------------------------------
## Config initializers
# --------------------------------------------
app.logger.removeHandler(default_handler)
cors = CORS(app)
atexit.register(lambda: scheduler.shutdown())


# --------------------------------------------
## View initializers
# --------------------------------------------
import dtapp.api
import dtapp.services
import dtapp.views


app.logger.info("axle started successfully")
