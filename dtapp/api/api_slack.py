import hashlib
import hmac
import time

from dtapp.main import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from dtapp.views.slack import handle_reaction_added, handle_approve_all_command, handle_request_access_command, handle_modal_submission, TENANT_ACCESS_MODAL_CALLBACK_ID

SLACK_SIGNING_SECRET = os.environ.get('SLACK_SIGNING_SECRET', '')


def _verify_slack_signature(req):
    timestamp = req.headers.get('X-Slack-Request-Timestamp', '')
    slack_signature = req.headers.get('X-Slack-Signature', '')

    # Reject requests older than 5 minutes to prevent replay attacks
    if abs(time.time() - float(timestamp)) > 300:
        return False

    body = req.get_data(as_text=True)
    sig_basestring = f"v0:{timestamp}:{body}".encode()
    computed = 'v0=' + hmac.new(SLACK_SIGNING_SECRET.encode(), sig_basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, slack_signature)


@cross_origin()
@app.route('/slack/events', methods=['POST'])
def api_slack_events():
    try:
        data = json.loads(request.data)

        app.logger.info(f"Received Slack event: {data.get('type')}")

        # Slack URL verification handshake
        if data.get('type') == 'url_verification':
            return jsonify({'challenge': data['challenge']}), 200

        if not _verify_slack_signature(request):
            return jsonify({'error': 'invalid signature'}), 403

        event = data.get('event', {})
        app.logger.info(f"Processing Slack event: {event.get('type')}")
        if event.get('type') == 'reaction_added':
            handle_reaction_added(event)

        return jsonify({'ok': True}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/slack/approve-all', methods=['POST'])
def api_slack_approve_all():
    try:
        if not _verify_slack_signature(request):
            return jsonify({'error': 'invalid signature'}), 403

        payload = request.form.to_dict()
        message = handle_approve_all_command(payload)

        return jsonify({'response_type': 'in_channel', 'text': message}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/slack/request-access', methods=['POST'])
def api_slack_request_access():
    try:
        if not _verify_slack_signature(request):
            return jsonify({'error': 'invalid signature'}), 403

        payload = request.form.to_dict()
        handle_request_access_command(payload)

        return '', 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/slack/interactions', methods=['POST'])
def api_slack_interactions():
    try:
        if not _verify_slack_signature(request):
            return jsonify({'error': 'invalid signature'}), 403

        payload = json.loads(request.form.get('payload', '{}'))
        interaction_type = payload.get('type')

        app.logger.info(f"Slack interaction received: type={interaction_type}")

        if interaction_type == 'view_submission':
            callback_id = payload.get('view', {}).get('callback_id')
            if callback_id == TENANT_ACCESS_MODAL_CALLBACK_ID:
                result = handle_modal_submission(payload)
                if result:
                    return jsonify(result), 200

        return '', 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
