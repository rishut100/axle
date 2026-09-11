from dtapp.views.infra import *
from dtapp.views.wraps import *
from dtapp.main import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/db/backup', methods=['POST'])
@api_login_required
def api_db_backup():
    try:
        if request.method == 'POST':
            app.logger.info("executing prod db backup")
            json_data = json.loads(request.data)

            if int(json_data['tenant_id']) < 60:
                return jsonify({'message': 'Error: Only prod tenants are allowed'}), 400

            thr = Thread(target=run_db_backup, args=[json_data['tenant_id']])
            thr.start()
            return jsonify({"message": "initiated"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/db/backup_restore_preprod', methods=['POST'])
@api_login_required
def api_db_backup_restore_preprod():
    try:
        if request.method == 'POST':
            app.logger.info("executing prod db backup and restore to preprod")
            json_data = json.loads(request.data)

            if int(json_data['destination_tenant_id']) < 31 or int(json_data['destination_tenant_id']) > 99:
                return jsonify({'message': 'Error: Please enter preprod tenant id'}), 400

            run_db_backup_restore_preprod(json_data['source_tenant_id'], json_data['destination_tenant_id'])
            return jsonify({"message": "initiated"}), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
