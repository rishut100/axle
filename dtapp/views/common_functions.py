import traceback
import logging
from flask import request


def log_exception(e, status_code=400):
    extras = {
        'http_status': status_code,
        'client_ip': getattr(request, 'remote_addr', ''),
        'method': getattr(request, 'method', ''),
        'url': getattr(request, 'path', ''),
        'exception_trace': traceback.format_exc()
    }
    
    # Choose log level based on HTTP status code
    if status_code >= 500:
        logging.error(str(e), extra=extras)
    elif status_code >= 400:
        logging.warning(str(e), extra=extras)
    else:
        logging.info(str(e), extra=extras)


def test():
    return
