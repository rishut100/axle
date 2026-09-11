import os
basedir = os.path.abspath(os.path.dirname(__file__))

# Common config params
ENV = 'prod'
DEBUG = False
TESTING = False

# Application threads. A common general assumption is
# using 2 per available processor cores - to handle
# incoming requests using one and performing background
# operations using the other.
THREADS_PER_PAGE = 2

# Enable protection agains *Cross-site Request Forgery (CSRF)*
CSRF_ENABLED = True

# Use a secure, unique and absolutely secret key for
# signing the data.
CSRF_SESSION_KEY = "dtadminapp2022"

# Secret key for signing cookies
SECRET_KEY = "dtadminapp2022"

# CORS content type
CORS_HEADERS = 'Content-Type'

# Celery broker parameters
CELERY_RESULT_BACKEND = 'amqp://admin:admin@localhost'
CELERY_BROKER_URL = 'amqp://admin:admin@localhost'

# AWS Constants
AWS_SECRET_NAME = "gcp-key"
GLOBAL_KEY_SECRET_NAME = "prod-secrets"
AWS_REGION = "us-east-1"

# Drive db details
DRIVE_DB = "drivetrain_highway"
DRIVE_HOST = "rds-highway.drivetrain.ai"
DRIVE_USER = "drive"
DRIVE_PORT = "5432"

DRIVE_V3_DB = "drivetrain_production"
DRIVE_V3_HOST = "rds-production.drivetrain.ai"
DRIVE_V3_PORT = "5432"

# GCP access DB details
GCP_ACCESS_DB_HOST = "rds-tools.drivetrain.ai"
GCP_ACCESS_DB_NAME = "gcp_access"
GCP_ACCESS_DB_PORT = "5432"
GCP_ACCESS_DB_USER = "drive"