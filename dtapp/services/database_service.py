import importlib

from dtapp.main import *
from dtapp.views.common_functions import log_exception


db_pass = os.environ.get('DB_PASS')


def get_v3_db_config():
    if app.config.get('AWS_REGION') == 'eu-central-1':
        prod_eu_config = importlib.import_module('env.prod-eu')
        return prod_eu_config.DRIVE_V3_DB, prod_eu_config.DRIVE_V3_HOST, prod_eu_config.DRIVE_V3_PORT
    return app.config['DRIVE_V3_DB'], app.config['DRIVE_V3_HOST'], app.config['DRIVE_V3_PORT']


def execute_db_query(query, update=False):
    result = []
    connection = None
    cursor = None
    try:
        app.logger.info("executing prod db query %s", query)
        connection = psycopg2.connect(
            database=app.config['DRIVE_DB'],
            host=app.config['DRIVE_HOST'],
            user=app.config['DRIVE_USER'],
            password=db_pass,
            port=app.config['DRIVE_PORT'],
            cursor_factory=RealDictCursor
        )
        connection.set_session(readonly=False if update is True else False)
        cursor = connection.cursor()
        cursor.execute(query)
        if update is False:
            result = cursor.fetchall()
        else:
            result = cursor.fetchone()
            connection.commit()
    except (Exception, psycopg2.Error) as error:
        log_exception(f"Failed to execute query: {error}", 500)
    finally:
        app.logger.info("prod db query ran. closing connection")
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return result


def execute_v3_db_query(query, update=False):
    result = []
    connection = None
    cursor = None
    try:
        app.logger.info("executing prod db query %s", query)
        db_name, db_host, db_port = get_v3_db_config()
        connection = psycopg2.connect(
            database=db_name,
            host=db_host,
            user=app.config['DRIVE_USER'],
            password=db_pass,
            port=db_port,
            cursor_factory=RealDictCursor
        )
        connection.set_session(readonly=False if update is True else False)
        cursor = connection.cursor()
        cursor.execute(query)
        if update is False:
            result = cursor.fetchall()
        else:
            result = cursor.fetchone()
            connection.commit()
    except (Exception, psycopg2.Error) as error:
        log_exception(f"Failed to execute query: {error}", 500)
    finally:
        app.logger.info("prod db query ran. closing connection")
        if cursor:
            cursor.close()
        if connection:
            connection.close()

    return result


def drop_db_query(query):
    connection = None
    cursor = None
    try:
        app.logger.info("executing prod db query %s", query)
        connection = psycopg2.connect(
            database=app.config['DRIVE_DB'],
            host=app.config['DRIVE_HOST'],
            user=app.config['DRIVE_USER'],
            password=db_pass,
            port=app.config['DRIVE_PORT'],
            cursor_factory=RealDictCursor
        )
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(query)
    except (Exception, psycopg2.Error) as error:
        log_exception(f"Failed to execute query: {error}", 500)
    finally:
        app.logger.info("prod db query ran. closing connection")
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def drop_v3_db_query(query):
    connection = None
    cursor = None
    try:
        app.logger.info("executing prod v3 db query %s", query)
        db_name, db_host, db_port = get_v3_db_config()
        connection = psycopg2.connect(
            database=db_name,
            host=db_host,
            user=app.config['DRIVE_USER'],
            password=db_pass,
            port=db_port,
            cursor_factory=RealDictCursor
        )
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(query)
    except (Exception, psycopg2.Error) as error:
        log_exception(f"Failed to execute query: {error}", 500)
    finally:
        app.logger.info("prod v3 db query ran. closing connection")
        if cursor:
            cursor.close()
        if connection:
            connection.close()
        

def run_gcp_access_query(query, params=None, fetch=True):
    conn = cur = None
    try:
        conn = psycopg2.connect(
            database=app.config['GCP_ACCESS_DB_NAME'],
            host=app.config['GCP_ACCESS_DB_HOST'],
            user=app.config['GCP_ACCESS_DB_USER'],
            password=db_pass,
            port=app.config['GCP_ACCESS_DB_PORT'],
            cursor_factory=RealDictCursor,
        )
        cur = conn.cursor()
        cur.execute(query, params or [])
        if fetch:
            return cur.fetchall()

        conn.commit()
        return None
    
    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
