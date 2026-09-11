from dtapp.views.okta import *
from dtapp.views.wraps import *
from dtapp.services.email_service import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/okta/password-updated', methods=['GET', 'POST'])
def api_okta_password_reset():
    try:
        if request.method == 'GET':
            app.logger.info("okta password reset webhook verification")
            result = {"verification": request.headers['x-okta-verification-challenge']}
            return jsonify(result), 200

        if request.method == 'POST':
            app.logger.info("Signing out of all drivetrain apps")
            json_data = json.loads(request.data)
            _result = okta_password_reset(json_data['data']['events'])

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/okta/new-user', methods=['GET', 'POST'])
def api_okta_new_user_alert():
    try:
        if request.method == 'GET':
            app.logger.info("okta new user webhook verification")
            result = {"verification": request.headers['x-okta-verification-challenge']}
            return jsonify(result), 200

        if request.method == 'POST':
            app.logger.info("executing sending email for okta new user")
            json_data = json.loads(request.data)
            user_tenant_id = get_okta_user_groups(json_data['data']['events'][0]['target'][0]['id'])
            user_name = json_data['data']['events'][0]['target'][0]['displayName']
            user_email = json_data['data']['events'][0]['target'][0]['alternateId']
            _result = okta_new_user_alert_mail(user_email, user_name, user_tenant_id)

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/okta/password-reset-alert', methods=['GET', 'POST'])
def api_okta_password_reset_alert():
    try:
        if request.method == 'GET':
            app.logger.info("okta user password reset webhook verification")
            result = {"verification": request.headers['x-okta-verification-challenge']}
            return jsonify(result), 200

        if request.method == 'POST':
            app.logger.info("executing sending email for okta password reset")
            json_data = json.loads(request.data)
            user_tenant_id = get_okta_user_groups(json_data['data']['events'][0]['target'][0]['id'])
            user_email = json_data['data']['events'][0]['target'][0]['alternateId']
            _result = okta_password_reset_alert_mail(user_email, user_tenant_id)

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/okta/delete-user-alert', methods=['GET', 'POST'])
def api_okta_delete_user_alert():
    try:
        if request.method == 'GET':
            app.logger.info("okta user delete webhook verification")
            result = {"verification": request.headers['x-okta-verification-challenge']}
            return jsonify(result), 200

        if request.method == 'POST':
            app.logger.info("executing sending email for okta delete user")
            json_data = json.loads(request.data)
            user_email = json_data['data']['events'][0]['target'][0]['alternateId']
            _result = okta_delete_user_alert_mail(user_email)

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
