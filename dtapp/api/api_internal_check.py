from dtapp.main import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from dtapp.views.internal_api_check import check_all_endpoints, check_single_endpoint


@cross_origin()
@app.route('/internal/check', methods=['GET'])
@api_login_required
def api_internal_check():
    try:
        result = check_all_endpoints()
        status_code = 200 if result["status"] == "passed" else 502
        return jsonify(result), status_code
    except Exception as e:
        log_exception(e, 500)
        return jsonify({"error": str(e)}), 500


@cross_origin()
@app.route('/internal/check/url', methods=['POST'])
@api_login_required
def api_internal_check_url():
    try:
        url = request.json.get("url") if request.json else None
        if not url:
            return jsonify({"error": "Missing required body parameter: url"}), 400
        result = check_single_endpoint(url)
        status_code = 200 if result["passed"] else 502
        return jsonify(result), status_code
    except Exception as e:
        log_exception(e, 500)
        return jsonify({"error": str(e)}), 500
