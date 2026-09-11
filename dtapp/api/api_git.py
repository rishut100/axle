from dtapp.views.git_rules import *
from dtapp.views.wraps import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/git/rule_check', methods=['POST'])
@api_login_required
def api_git_rule_check():
    try:
        if request.method == 'POST':
            app.logger.info("running git rules check")
            _result = git_repo_status_check()
            return jsonify(_result), 200

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400


@cross_origin()
@app.route('/git/create_repo', methods=['POST'])
@api_login_required
def api_git_create_repo():
    try:
        body = request.get_json() or {}
        repo_name = body.get('repo_name', '').strip()
        initiated_by = body.get('initiated_by', '').strip()
        branch_name = body.get('branch_name', '').strip()
        collaborators = body.get('collaborators', [])
        teams = body.get('teams', [])

        if not repo_name:
            return jsonify({"error": "repo_name is required"}), 400
        if not initiated_by:
            return jsonify({"error": "initiated_by is required"}), 400
        if not branch_name:
            return jsonify({"error": "branch_name is required"}), 400

        app.logger.info(f"Creating/updating git repo '{repo_name}' initiated by '{initiated_by}'")
        result, status_code = create_git_repo(repo_name, initiated_by, branch_name, collaborators, teams)
        return jsonify(result), status_code

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400