from dtapp.views.email_query import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from dtapp.views.bigquery_access import *


@cross_origin()
@app.route('/bigquery/email_query', methods=['GET', 'POST', 'PUT', 'DELETE'])
@api_login_required
def api_execute_query():
    try:
        if request.method == 'POST':
            app.logger.info("create bq email query request")
            json_data = json.loads(request.data)

            _result = create_email_query(json_data['tenant'], json_data['source_app'], json_data['subject'],
                                        json_data['big_query'], json_data['email_recipients'], json_data['email_content'],
                                        json_data['cron'])

            if (_result['ResponseMetadata']['HTTPStatusCode'] == 200):
                return jsonify({'msg': 'success'}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400


        if request.method == 'GET':
            app.logger.info("get all bq data queries")
            _result = get_all_email_query()

            if _result['Items']:
                return jsonify(_result['Items']), 200
            else:
                return jsonify({'msg': 'failed', 'result': 'No data found'}), 400


        if request.method == 'PUT':
            json_data = json.loads(request.data)
            app.logger.info(f"Updating email query")

            _result = update_email_query(json_data['tenant'], json_data['source_app'], json_data['subject'],
                                        json_data['big_query'], json_data['email_recipients'], json_data['email_content'],
                                        json_data['cron'])

            if (_result['ResponseMetadata']['HTTPStatusCode'] == 200):
                return jsonify({'msg': 'success'}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400


        if request.method == 'DELETE':
            json_data = json.loads(request.data)
            app.logger.info(f"Deleting email query for {json_data['tenant']}, {json_data['subject']}")

            _result = delete_email_query(json_data['tenant'], json_data['subject'])

            if (_result['ResponseMetadata']['HTTPStatusCode'] == 200):
                return jsonify({'msg': 'deleted'}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/email_query/tenant/<tenant_id>', methods=['GET'])
@api_login_required
def api_tenant_execute_query(tenant_id):
    try:
        if request.method == 'GET':
            app.logger.info(f"get email query for tenant {tenant_id}")

            _result = get_tenant_email_query(tenant_id)

            if _result['Items']:
                return jsonify(_result['Items']), 200
            else:
                return jsonify({'msg': 'failed', 'result': 'No data found'}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/email_query/trigger', methods=['POST'])
@api_login_required
def api_trigger_query():
    try:
        if request.method == 'POST':
            json_data = json.loads(request.data)
            app.logger.info(f"Triggering manual email query alert for {json_data['tenant']}")

            _result = trigger_email_query(json_data['tenant'])

            if (_result['ResponseMetadata']['HTTPStatusCode'] == 200):
                return jsonify({'msg': 'success'}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/table_list', methods=['POST'])
@api_login_required
def api_bq_table_list():
    try:
        if request.method == 'POST':
            json_data = json.loads(request.data)
            app.logger.info(f"Getting bq table list for {json_data['tenant']}")

            _result = check_unused_primary_tables(json_data['tenant'])

            if _result:
                return jsonify({'result': _result}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
