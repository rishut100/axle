import os
basedir = os.path.abspath(os.path.dirname(__file__))

# Common config params
ENV = 'dev'
DEBUG = True
TESTING = False

# Application threads. A common general assumption is
# using 2 per available processor cores - to handle
# incoming requests using one and performing background
# operations using the other.
THREADS_PER_PAGE = 2

# Secret key for signing cookies
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-key')

# CORS content type
CORS_HEADERS = 'Content-Type'

# Celery broker parameters
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
CELERY_BROKER_URL = 'redis://localhost:6379/0'

# AWS Constants
AWS_SECRET_NAME = "REPLACE_WITH_SECRET_NAME"
GLOBAL_KEY_SECRET_NAME = "REPLACE_WITH_SECRET_NAME"
AWS_REGION = "us-east-1"

# Drive db details
DRIVE_DB = "product_stagingv2"
DRIVE_HOST = "rds-staging.example.internal"
DRIVE_USER = "drive"
DRIVE_PORT = "5432"

DRIVE_V3_DB = "product_stagingv2"
DRIVE_V3_HOST = "rds-staging.example.internal"
DRIVE_V3_PORT = "5432"

# GCP access DB details
GCP_ACCESS_DB_HOST = "localhost"
GCP_ACCESS_DB_NAME = "gcp_access"
GCP_ACCESS_DB_PORT = "5432"
GCP_ACCESS_DB_USER = "postgres"