import boto3, os, time
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from dtapp.services.database_service import run_gcp_access_query
from dtapp.main import app

_region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb_client = boto3.client('dynamodb', region_name=_region)
dynamodb_resource = boto3.resource('dynamodb', region_name=_region)
app_env = os.environ.get('DTADMIN_ENV')
account_table_name = app_env + '_accounts'
data_alerts_table_name = app_env + '_data_alerts'
scheduled_emails_table_name = app_env + '_scheduled_emails'
bq_access_requests_table_name = 'bq_access_requests' #postgres table


def accounts_table():
    try:
        table = dynamodb_client.create_table(
            TableName=account_table_name,
            KeySchema=[
                {
                    'AttributeName': 'email',
                    'KeyType': 'HASH'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'email',
                    'AttributeType': 'S'
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            },
        )

    except dynamodb_client.exceptions.ResourceInUseException:
        pass
    except ClientError as err:
        app.logger.error(
            "Couldn't create table %s. Error: %s: %s",
            table,
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
    finally:
        return dynamodb_resource.Table(account_table_name)


def data_alerts_table():
    try:
        table = dynamodb_client.create_table(
            TableName=data_alerts_table_name,
            KeySchema=[
                {
                    'AttributeName': 'tenant',
                    'KeyType': 'HASH'
                },
                {
                    'AttributeName': 'subject',
                    'KeyType': 'RANGE'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'tenant',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'subject',
                    'AttributeType': 'S'
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            },
        )

    except dynamodb_client.exceptions.ResourceInUseException:
        pass
    except ClientError as err:
        app.logger.error(
            "Couldn't create table %s. Error: %s: %s",
            table,
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
    finally:
        return dynamodb_resource.Table(data_alerts_table_name)


def scheduled_emails_table():
    try:
        table = dynamodb_client.create_table(
            TableName=scheduled_emails_table_name,
            KeySchema=[
                {
                    'AttributeName': 'tenant',
                    'KeyType': 'HASH'
                },
                {
                    'AttributeName': 'subject',
                    'KeyType': 'RANGE'
                }
            ],
            AttributeDefinitions=[
                {
                    'AttributeName': 'tenant',
                    'AttributeType': 'S'
                },
                {
                    'AttributeName': 'subject',
                    'AttributeType': 'S'
                }
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 1,
                'WriteCapacityUnits': 1
            },
        )

    except dynamodb_client.exceptions.ResourceInUseException:
        pass
    except ClientError as err:
        app.logger.error(
            "Couldn't create table %s. Error: %s: %s",
            table,
            err.response["Error"]["Code"],
            err.response["Error"]["Message"],
        )
    finally:
        return dynamodb_resource.Table(scheduled_emails_table_name)


"""
status codes:
0 - pending
1 - approved
2 - rejected
revoked -> 0 (not revoked), 1 (revoked)
"""
def bq_access_requests_table():
    try:
        if (app_env or '').lower() != 'prod-eu':
            with app.app_context():
                create_table_query = f"""
                CREATE TABLE IF NOT EXISTS public.{bq_access_requests_table_name} (
                    request_id UUID PRIMARY KEY,
                    project_id VARCHAR(255) NOT NULL,
                    email VARCHAR(255) NOT NULL,
                    tenant_id VARCHAR(255) NOT NULL,
                    access_level VARCHAR(50) NOT NULL,
                    approved_by VARCHAR(50),
                    reason TEXT NOT NULL,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    revoked SMALLINT NOT NULL DEFAULT 0,
                    status SMALLINT NOT NULL DEFAULT 0
                );
                """
                table_exists_query = f"""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'bq_access_requests'
                );
                """

                table_created = run_gcp_access_query(table_exists_query, fetch=True)

                if table_created[0]['exists']:
                    app.logger.warning(f"Table {bq_access_requests_table_name} already exists")
                else:
                    run_gcp_access_query(create_table_query, fetch=False)

                    app.logger.info(f"Created Table {bq_access_requests_table_name} for Big Query Access Requests")
                return True

    except Exception as e:
        app.logger.error(f"Error creating table {bq_access_requests_table_name}: {e}")
        return False


def execute_model():
    return accounts_table(), data_alerts_table(), scheduled_emails_table(), bq_access_requests_table()
