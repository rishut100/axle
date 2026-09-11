from dtapp.views.scheduler import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/scheduler/run', methods=['POST'])
def api_scheduler_run():
    try:
        if request.method == 'POST':
            app.logger.info("Running all schedulers")
            schedule_scheduled_emails()
            schedule_email_query()
            schedule_git_repo_check()
            schedule_revoke_expired_access()
            return jsonify({"message": "scheduled"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/scheduler/email_query', methods=['POST'])
@api_login_required
def api_scheduler_email_query():
    try:
        if request.method == 'POST':
            app.logger.info("creating schedule for email query")
            _result = schedule_email_query()
            return jsonify({"message": "scheduled"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/scheduler/scheduled_emails', methods=['POST'])
@api_login_required
def api_scheduler_scheduled_emails():
    try:
        if request.method == 'POST':
            app.logger.info("creating schedule for scheduled emails")
            _result = schedule_scheduled_emails()
            return jsonify({"message": "scheduled"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/scheduler', methods=['GET'])
@api_login_required
def api_scheduler():
    try:
        if request.method == 'GET':
            app.logger.info("get all scheduled jobs")
            _result = get_scheduled_jobs()
            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
