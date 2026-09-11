from dtapp.views.tenant import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/tenant/get_active_prod', methods=['GET'])
@api_login_required
def api_get_active_tenants():
    try:
        if request.method == 'GET':
            app.logger.info("Getting active prod tenants")
            _result = get_active_tenant_list()

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant/get_all_prod', methods=['GET'])
@api_login_required
def api_get_all_tenants():
    try:
        if request.method == 'GET':
            app.logger.info("Getting all prod tenants")
            _result = get_all_tenant_list()

            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/cloneTenant', methods=['POST'])
@api_login_required
def api_clone_tenant():
    try:
        json_data = json.loads(request.data)
        destination_tenant_id = json_data['destination_id']
        source_tenant_id = json_data['source_id']

        source_bq_schema = f"tenant{source_tenant_id}"
        destination_bq_schema = json_data['destination_schema']
        is_sandbox_tenant = json_data['is_sandbox_tenant']

        app.logger.info(f"Tenant cloning started for tenant {source_tenant_id} and payload - {json_data}")

        if not is_sandbox_tenant:
            app.logger.info(f"Checking tenant reset state for {destination_bq_schema}")
            has_been_reset = is_tenant_reset(destination_tenant_id, destination_bq_schema, is_sandbox_tenant)
            if not has_been_reset:
                return jsonify({"message": "Destination tenant is not reset"}), 400

        app.logger.info(f"Copying DB for the source tenant {source_tenant_id}")
        current_date = datetime.today().strftime("%Y_%m_%d")
        file_name = f"/tmp/{source_tenant_id}_backup_{str(current_date)}.sql"
        _postgres_return = backup_restore_schema(source_tenant_id, destination_tenant_id, file_name, is_sandbox_tenant)
        if not _postgres_return:
            return jsonify({"message": "Tenant schema copy is failed"}), 400

        app.logger.info(f"Copy dataset for tenant {destination_bq_schema}")
        if not is_sandbox_tenant:
            ## Sandbox tenants not needed to copy the bq primary schema datasets, as it's get built using dbt execution.
            _bq_return = copy_primary_dataset(source_tenant_id, destination_tenant_id, source_bq_schema, destination_bq_schema, is_sandbox_tenant)
            if not _bq_return:
                return jsonify({"message": "Primary schema copy is failed"}), 400
        else:
            _bq_return = copy_lists_table(source_tenant_id, destination_tenant_id, source_bq_schema, destination_bq_schema, is_sandbox_tenant)
            if not _bq_return:
                return jsonify({"message": "Lists table copy is failed"}), 400

        return jsonify({"message": "success"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/tenant/sandbox', methods=['POST'])
@api_login_required
def api_create_sandbox_env():
    try:
        if request.method == 'POST':
            json_data = json.loads(request.data)
            tenant_id = json_data['tenant_id']

            # Checking existing liquibase migration run
            app.logger.info("Checking existing liquibase restart / migration run")
            if check_liquibase_restart() and check_liquibase_migration():
                app.logger.info("No liquibase restart / migration running running right now. Proceeding with liquibase restart")
            else:
                return jsonify({"message": "Liquibase restart / migration already running. Try after 5 min."}), 400

            # Checking whether sandbox is present or not and add the sandbox entry in tenant db
            if tenant_exists(tenant_id) and not sandbox_exists(tenant_id):
                add_sandbox_entry(tenant_id)

                return jsonify({"message": "success"}), 200
            else:
                return jsonify({"message": "failed. Invalid tenant id or sandbox tenant already exists"}), 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
