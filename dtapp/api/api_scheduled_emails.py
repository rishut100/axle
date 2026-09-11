from dtapp.views.scheduled_emails import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/scheduled_emails', methods=['GET', 'POST'])
@api_login_required
def api_scheduled_emails():
    try:
        if request.method == 'POST':
            app.logger.info("creating scheduled emails")
            json_data = json.loads(request.data)

            _result = create_scheduled_emails(json_data['tenant'], json_data['source_app'], json_data['email_recipients'], json_data['subject'], json_data['email_content'], json_data['params'], json_data['cron'])

            if (_result['ResponseMetadata']['HTTPStatusCode'] == 200):
                return jsonify({'msg': 'success'}), 200
            else:
                return jsonify({'msg': 'failed', 'result': _result}), 400

        if request.method == 'GET':
            app.logger.info("get all scheduled emails")

            _result = get_all_scheduled_emails()

            if _result['Items']:
                return jsonify(_result['Items']), 200
            else:
                return jsonify({'msg': 'failed', 'result': 'No data found'}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
