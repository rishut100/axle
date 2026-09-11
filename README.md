# Admin Access Grant
This app is for internal purpose

# Development Setup
1. Install python 3.8 and create virtual env for the same
   1. "python3.8 -m venv ." - This will create a virtual env on the current folder
   2. Activate the venv - "source venv_location/bin/activate"
2. Install all the requirements using "pip install -r requirements.txt"
3. Create a env variable "export DTADMIN_ENV=dev".
4. Run the code - python run.py

# Postman collection
Postman collection is found in the docs folder.

# DB setup
1. Install postgres 14 and create a database
2. Change the database config in env/dev.py file

# Auth token
1. Signup using postman signup api
2. Activate it using the activation key
3. Login using postman api to get the auth token
4. Create API key auth in postman using the key as "x-access-token" and auth token as value

