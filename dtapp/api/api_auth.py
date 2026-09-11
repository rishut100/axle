from dtapp.main import *
from dtapp.views.auth import *
from dtapp.views.common_functions import log_exception


@cross_origin()
@app.route('/signup', methods=['POST'])
def api_signup():
    try:
        app.logger.info("signup request")
        json_data = json.loads(request.data)
        
        _response = signup(json_data['email'], json_data['password'])
        
        if (_response['ResponseMetadata']['HTTPStatusCode'] == 200):
            return {'msg': 'success'}, 200
        else:
            return {'msg': 'failed', 'result': _response}, 400

    except Exception as e:
        log_exception(e, 400)
        return jsonify({"error": str(e)}), 400
