#!flask/bin/python
from dtapp.main import app
import os

app.secret_key = os.urandom(12)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5002, use_reloader=False)
