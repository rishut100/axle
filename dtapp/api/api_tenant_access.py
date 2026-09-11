from dtapp.views.tenant_access import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


def _is_v3(tenant_id):
    try:
        return int(tenant_id) >= 400
    except (TypeError, ValueError):
        return False


@cross_origin()
@app.route('/tenant_access/create', methods=['POST'])
@api_login_required
def api_create_tenant_access():
    try:
        json_data = json.loads(request.data)
        tenant_id = json_data['tenant_id']
        user_email = json_data['user_email']
        validity = json_data['validity']
        reason = json_data['reason']
        v3 = _is_v3(tenant_id)
        app.logger.info(f"Creating tenant access entry (v3={v3})")
        _result = create_tenant_access(tenant_id=tenant_id, user_email=user_email, validity=validity, reason=reason, v3=v3)
        return jsonify(_result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant_access/update', methods=['PATCH'])
@api_login_required
def api_update_tenant_access():
    try:
        json_data = json.loads(request.data)
        tenant_id = json_data.get('tenant_id')
        ids = json_data['ids']
        _status = json_data['status']
        approved_by = json_data['approved_by']
        v3 = _is_v3(tenant_id)
        app.logger.info(f"Updating tenant access entry (v3={v3})")
        _result = update_tenant_access(ids=ids, status=_status, approved_by=approved_by, v3=v3)
        return jsonify(_result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant_access/pending', methods=['GET'])
@api_login_required
def api_pending_tenant_access():
    try:
        app.logger.info("Getting pending tenant access approvals")
        v2_result = get_tenant_access(v3=False, status=0)
        v3_result = get_tenant_access(v3=True, status=0)
        return jsonify(v2_result + v3_result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant_access/approved', methods=['GET'])
@api_login_required
def api_get_tenant_access():
    try:
        app.logger.info("Getting approved tenant access")
        v2_result = get_tenant_access(v3=False, status=1)
        v3_result = get_tenant_access(v3=True, status=1)
        return jsonify(v2_result + v3_result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant_access/<id>', methods=['GET'])
@api_login_required
def api_approve_tenant_access_id(id):
    try:
        if request.method == 'GET':
            _result = get_tenant_access(id=id)

            return jsonify(_result), 200
    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant_access_v3/<id>', methods=['GET'])
@api_login_required
def api_approve_tenant_access_id_v3(id):
    try:
        if request.method == 'GET':
            _result = get_tenant_access(v3=True, id=id)

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
