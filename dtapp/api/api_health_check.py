from dtapp.main import *
from dtapp.services.email_service import *
from dtapp.views.common_functions import log_exception


@app.route('/health', methods=['GET'])
def api_health_check():
    try:
        return jsonify({"message": "ok"}), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
    

@app.route('/favicon.ico')
def favicon():
    return '', 204
