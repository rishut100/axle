from dtapp.views.tenant_v3 import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/tenant/get_active_prod_v3', methods=['GET'])
@api_login_required
def api_get_active_tenants_v3():
    try:
        if request.method == 'GET':
            app.logger.info("Getting active prod tenants")
            _result = get_active_tenant_list_v3()

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant/subdomain', methods=['GET'])
@api_login_required
def api_get_domain_v3():
    try:
        if request.method == 'GET':
            tenant_id = request.args.get('id')
            app.logger.info(f"Getting domain for the given tenant {tenant_id}")
            _result = get_tenant_domain(tenant_id)

            if not _result:
                return jsonify(_result), 404

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 404)
        return jsonify({"error": str(e)}), 404


@cross_origin()
@app.route('/tenant/get_all_prod_v3', methods=['GET'])
@api_login_required
def api_get_all_tenants_v3():
    try:
        if request.method == 'GET':
            app.logger.info("Getting all prod tenants")
            _result = get_all_tenant_list_v3()

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/cloneTenantV3', methods=['POST'])
@api_login_required
def api_clone_tenant_v3():
    try:
        json_data = json.loads(request.data)
        destination_tenant_id = json_data['destination_id']
        source_tenant_id = json_data['source_id']

        source_bq_schema = f"tenant{source_tenant_id}"
        destination_bq_schema = f"tenant{destination_tenant_id}"

        app.logger.info(f"Tenant cloning started for tenant {source_tenant_id} and payload - {json_data}")

        # Checking for Tenant reset state
        app.logger.info(f"Checking tenant reset state for {destination_bq_schema}")
        has_been_reset = is_tenant_reset(destination_tenant_id, destination_bq_schema)
        if not has_been_reset:
            return jsonify({"message": "Destination tenant is not reset"}), 400

        # Copying DB for the source tenant to destination tenant
        app.logger.info(f"Copying DB for the source tenant {source_tenant_id}")
        current_date = datetime.today().strftime("%Y_%m_%d")
        file_name = f"/tmp/{source_tenant_id}_backup_{str(current_date)}.sql"
        _postgres_return = backup_restore_schema(source_tenant_id, destination_tenant_id, file_name)
        if not _postgres_return:
            return jsonify({"message": "Tenant schema copy is failed"}), 400

        # Copying src dataset for the source tenant to destination tenant using xiphos endpoint
        app.logger.info(f"Copy dataset for tenant {destination_bq_schema}")
        _bq_return = copy_primary_dataset(source_tenant_id, destination_tenant_id, source_bq_schema, destination_bq_schema)
        if not _bq_return:
            return jsonify({"message": "Primary schema copy is failed"}), 400

        _bq_src_return = copy_src_dataset(source_tenant_id, destination_tenant_id, source_bq_schema, destination_bq_schema)
        if not _bq_src_return:
            return jsonify({"message": "Src schema copy is failed"}), 400

        return jsonify({"message": "success"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
