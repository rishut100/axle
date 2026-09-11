from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from twilio.twiml.voice_response import VoiceResponse, Dial, Say
from dtapp.views.twilio_app import *
from dtapp.views.twilio_app import get_oncall_numbers
from urllib.parse import quote

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "+16503904045")
TWILIO_BASE_URL = os.getenv("TWILIO_BASE_URL", "")
ALLOWED_CHANNEL_IDS = {"C0A8F32EV8D", "C02LTSTTRB5"}

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

@app.route("/datadog/webhook", methods=["POST"])
def datadog_webhook():
    """
    Receive webhook from Datadog with incident details.
    Initiates a Twilio call to the oncall owner.
    """
    try:
        payload = request.get_json()
        
        try:
            app.logger.info(payload["owner"])
            name = payload["owner"]
        except Exception:
            name = None
        
        app.logger.info(f"Received Datadog webhook: {payload}")
        
        incident_title = payload.get("title") or payload.get("event_title", "Production Alert")
        
        # Get oncall owner numbers (primary + secondary)
        oncall_numbers = get_oncall_numbers(name)

        twiml_url = f"{TWILIO_BASE_URL}/twilio/voice/connect?incident_title={quote(incident_title)}"

        try:
            call_sids = []
            for number in oncall_numbers:
                call = twilio_client.calls.create(
                    to=number,
                    from_=TWILIO_PHONE_NUMBER,
                    url=twiml_url,
                    method="POST",
                    status_callback=f"{TWILIO_BASE_URL}/twilio/status",
                    status_callback_event=["initiated", "ringing", "answered", "completed"],
                    status_callback_method="POST",
                )
                app.logger.info(f"Twilio call initiated: {call.sid} to {number}")
                call_sids.append(call.sid)

            return jsonify({
                "status": "success",
                "message": "Call initiated successfully",
                "call_sids": call_sids,
                "to": oncall_numbers,
                "incident_title": incident_title,
            }), 200
            
        except TwilioRestException as e:
            log_exception(f"Twilio API error: {e}", 500)
            return jsonify({
                "status": "error",
                "message": f"Failed to initiate call: {str(e)}"
            }), 500
            
    except Exception as e:
        log_exception(f"Error processing webhook: {e}", 500)
        return jsonify({
            "status": "error",
            "message": f"Error processing webhook: {str(e)}"
        }), 500


@app.route("/twilio/voice/connect", methods=["GET", "POST"])
def twilio_voice_connect():
    """
    TwiML endpoint that handles the call when Twilio connects to the oncall owner.
    This is called by Twilio when the call is answered.
    """
    try:
        incident_title = request.args.get('incident_title')
        app.logger.info(f"Incident title: {incident_title}")
        
        vr = VoiceResponse()
        vr.say(
            f"Production Alert: {incident_title}. Please look at the dashboard to get more details.",
            voice="alice"
        )
        
        vr.hangup()
        
        twiml_response = str(vr)
        app.logger.info(f"TwiML response: {twiml_response}")
        
        response = Response(twiml_response, mimetype="application/xml")
        response.headers["Content-Type"] = "application/xml; charset=utf-8"
        return response
    
    except Exception as e:
        log_exception(f"Error processing twilio voice connect: {e}", 500)
        return jsonify({
            "status": "error",
            "message": f"Error processing twilio voice connect: {str(e)}"
        }), 500


@app.route("/twilio/status", methods=["GET", "POST"])
def twilio_status_callback():
    """
    Receive status updates from Twilio about the call.
    """
    form = request.form.to_dict() if request.method == "POST" else request.args.to_dict()
    call_sid = form.get("CallSid")
    call_status = form.get("CallStatus")
    
    app.logger.info(f"Call status update - SID: {call_sid}, Status: {call_status}, Form: {form}")
    
    return Response(status=200)


@app.route("/twilio/slack/call", methods=["POST"])
def twilio_slack_call():
    try:
        app.logger.info(f"Received Slack call")
        text = request.form.get("text", "").strip().lower()
        channel_id = request.form.get("channel_id")
        
        if channel_id not in ALLOWED_CHANNEL_IDS:
            return jsonify({
                "response_type": "ephemeral",
                "text": (
                    f"❌  `/oncall` cannot be used in this channel. Please switch to the #oncall channel."
                )
            })
        
        # if text == "call":
        #     primary = get_oncall_number()
        #     oncall_numbers = [primary] if primary else []

        elif text.startswith("call "):
            oncall_numbers = get_oncall_numbers(text.split("@")[1].split(" ")[0])
        
        elif text == "help":
            return {
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*🚨 oncall Command Help*"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "`/oncall help` — Show oncall command help\n"
                                "`/oncall` — Show oncall owner schedule\n"
                                "`/oncall call` — Calls current oncall primary owner of the week\n"
                                "`/oncall call @[username]` — Call specific person by name, eg `/oncall call @kabi`"
                            )
                        }
                    }
                ]
            }
        
        elif text == "":
            app.logger.info(f"Received oncall user list request")
            oncall_list = get_oncall_user_list()
            if oncall_list:
                return jsonify({
                    "response_type": "in_channel",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "📟 *oncall owner schedule*"
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": oncall_list
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "To initiate a call, please use `/oncall call @[username]`."
                            }
                        }
                    ]
                }), 200
        
        else:
            return {
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "* Invalid Command: Please use `/oncall help` to see the available commands*"
                        }
                    }
                ]
            }
        
        twiml_url = f"{TWILIO_BASE_URL}/twilio/call"

        if oncall_numbers:
            try:
                call_sids = []
                for number in oncall_numbers:
                    call = twilio_client.calls.create(
                        to=number,
                        from_=TWILIO_PHONE_NUMBER,
                        url=twiml_url,
                        method="POST",
                        status_callback=f"{TWILIO_BASE_URL}/twilio/status",
                        status_callback_event=["initiated", "ringing", "answered", "completed"],
                        status_callback_method="POST",
                    )
                    app.logger.info(f"Twilio call initiated: {call.sid} to {number}")
                    call_sids.append(call.sid)

                return jsonify({
                    "response_type": "in_channel",
                    "text": f"🚨 oncall call triggered successfully to {len(call_sids)} number(s)."
                }), 200

            except TwilioRestException as e:
                app.logger.error(f"Twilio API error: {e}")
                log_exception(e, 500)
                return jsonify({
                    "status": "error",
                    "message": f"Failed to initiate call: {str(e)}"
                }), 500
        else:
            return {
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*No oncall owner found: Please use `/oncall help` to see the available commands*"
                        }
                    }
                ]
            }
            
    except Exception as e:
        log_exception(f"Error processing webhook: {e}", 500)
        return jsonify({
            "status": "error",
            "message": f"Error processing webhook: {str(e)}"
        }), 500


@app.route("/twilio/call", methods=["POST"])
def twilio_call():
    try:
        vr = VoiceResponse()
        vr.say(
            f"Production Alert: Call is initiated from slack. Please look at the dashboard to get more details.",
            voice="alice"
        )
        
        vr.hangup()
        
        twiml_response = str(vr)
        app.logger.info(f"TwiML response: {twiml_response}")
        
        response = Response(twiml_response, mimetype="application/xml")
        response.headers["Content-Type"] = "application/xml; charset=utf-8"
        return response
    
    except Exception as e:
        log_exception(f"Error processing twilio voice connect: {e}", 500)
        return jsonify({
            "status": "error",
            "message": f"Error processing twilio voice connect: {str(e)}"
        }), 500
