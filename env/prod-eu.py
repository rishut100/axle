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
CSRF_SESSION_KEY = os.environ.get("CSRF_SESSION_KEY", "")

# Secret key for signing cookies
SECRET_KEY = os.environ.get("SECRET_KEY", "")

# CORS content type
CORS_HEADERS = 'Content-Type'

# Celery broker parameters
CELERY_RESULT_BACKEND = 'amqp://admin:admin@localhost'
CELERY_BROKER_URL = 'amqp://admin:admin@localhost'

# AWS Constants
AWS_SECRET_NAME = "REPLACE_WITH_SECRET_NAME"
GLOBAL_KEY_SECRET_NAME = "REPLACE_WITH_SECRET_NAME"
AWS_REGION = "eu-central-1"

# Drive db details
DRIVE_DB = "product_highway"
DRIVE_HOST = "rds-highway.example.internal"
DRIVE_USER = "drive"
DRIVE_PORT = "5432"

DRIVE_V3_DB = "product_production_eu"
DRIVE_V3_HOST = "rds-eu-production.example.internal"
DRIVE_V3_PORT = "5432"

# GCP access DB details
GCP_ACCESS_DB_HOST = "rds-tools.example.internal"
GCP_ACCESS_DB_NAME = "gcp_access"
GCP_ACCESS_DB_PORT = "5432"
GCP_ACCESS_DB_USER = "drive"
