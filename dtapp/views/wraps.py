from dtapp.main import *
from flask import g
import traceback
from boto3.dynamodb.conditions import Attr


def api_login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        token = None
        if 'x-access-token' in request.headers:
            token = request.headers['x-access-token']

        if not token:
            return jsonify({'message': 'Token not found'}), 401

        from dtapp.main import tbl_accounts_client
        response = tbl_accounts_client.scan(FilterExpression=Attr("activation_key").eq(token))

        if response['Items'] and response['Items'][0]['activation_key'] == token:
            return f(*args, **kwargs)
        else:
            return jsonify({'message': 'Invalid Token'}), 401

    return wrap


@app.after_request
def custom_logger(response):
    try:
        if hasattr(response, 'get_data'):
            body = response.get_data(as_text=True)
        else:
            raw = getattr(response, 'data', '')
            if isinstance(raw, (bytes, bytearray)):
                body = raw.decode('utf-8', errors='replace')
            else:
                body = str(raw)
    except Exception:
        body = ''
    if body:
        body = body.replace('\r\n', '\n').replace('\n', '\\n').replace('\t', ' ')
        body = re.sub(r' {2,}', ' ', body)

    if body and len(body) > 1000:
        body = body[:1000] + '...[truncated]'

    # Get client_ip from headers first, then fall back to remote_addr
    client_ip = None
    if 'Client-Ip' in request.headers:
        client_ip = request.headers['Client-Ip'].split(',')[0].strip()
    
    if not client_ip:
        client_ip = getattr(request, 'remote_addr', '')
    
    extras = {
        'http_status': getattr(response, 'status_code', ''),
        'client_ip': client_ip,
        'method': getattr(request, 'method', ''),
        'url': getattr(request, 'path', '')
    }

    # Choose log level based on HTTP status code
    try:
        sc = int(extras['http_status']) if extras['http_status'] != '' else 0
    except Exception:
        sc = 0

    if sc >= 500:
        log_func = logging.error
    elif sc >= 400:
        log_func = logging.warning
    else:
        log_func = logging.info

    log_func(body, extra=extras)
    return response


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler that formats errors properly."""
    # Use custom client_ip if set, otherwise fall back to remote_addr
    client_ip = getattr(g, 'client_ip', None) or getattr(request, 'remote_addr', '')
    
    # Get request details
    extras = {
        'http_status': 500,
        'client_ip': client_ip,
        'method': getattr(request, 'method', ''),
        'url': getattr(request, 'path', ''),
        'exception_trace': traceback.format_exc()
    }
    
    # Log the error with proper formatting
    logging.error(str(e), extra=extras)
    
    # Return error response
    return jsonify({'error': str(e)}), 500