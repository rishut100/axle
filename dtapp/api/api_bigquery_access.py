from dtapp.views.email_query import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception
from dtapp.views.bigquery_access import *


@cross_origin()
@app.route('/bigquery/access', methods=['POST'])
@api_login_required
def api_request_bq_access_create():
    try:
        payload = json.loads(request.data)
        result = request_bq_access(payload)
        return jsonify(result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery_access/<id>', methods=['GET'])
@api_login_required
def api_request_bq_access(id):
    try:
        if request.method == 'GET':
            result = get_bq_access(id)
            if result[0]['status'] == 0:
                result[0]['status'] = "pending"
            elif result[0]['status'] == 1:
                result[0]['status'] = "approved"
            elif result[0]['status'] == 2:
                result[0]['status'] = "rejected"
            return jsonify(result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/access/reject', methods=['PATCH'])
@api_login_required
def api_reject_bq_access():
    try:
        data = json.loads(request.data)
        request_id = data["id"]
        rejected_by = data["rejected_by"]

        result = reject_bq_access(request_id, rejected_by)
        return jsonify(result), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/access/approve', methods=['PATCH'])
@api_login_required
def api_approve_bq_access():
    try:
        data = json.loads(request.data)
        request_id = data["id"]
        approved_by = data["approved_by"]

        result = approve_bq_access(request_id, approved_by)
        return jsonify(result), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/access/pending', methods=['GET'])
@api_login_required
def api_get_pending_bq_access():
    try:
        result = run_gcp_access_query(
            """
            SELECT request_id, access_level, email, tenant_id, project_id, expires_at
            FROM bq_access_requests
            WHERE status = 0 AND revoked = 0
            ORDER BY created_at DESC
            """
        )
        for x in result:
            x['link'] = f"https://your-retool-domain.retool.com/app/bigquery_access#id={x['request_id']}"
        return jsonify(result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/bigquery/access/approved', methods=['GET'])
@api_login_required
def api_get_approved_bq_access():
    try:
        result = run_gcp_access_query(
            """
            SELECT access_level, email, tenant_id, project_id, expires_at, approved_by
            FROM bq_access_requests
            WHERE status = 1 AND revoked = 0
            ORDER BY approved_by DESC
            """
        )
        return jsonify(result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


# This is not being used currently but keeping it to trigger revoks manually if needed
@cross_origin()
@app.route('/bigquery/access/revoke', methods=['POST'])
@api_login_required
def api_request_bq_access_revoke():
    try:
        revoke_expired_access()
        result = "revoked scheduler ran"
        return jsonify({"message": result}), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
