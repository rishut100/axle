#!flask/bin/python
from dtapp.main import app
import os
from waitress import serve

app.secret_key = os.urandom(12)

if __name__ == '__main__':
    serve(app, host="0.0.0.0", port=8080, channel_timeout=1200)
