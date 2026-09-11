def signup(email, password):
    
    from dtapp.main import app, hashlib, random, tbl_accounts_client, datetime
    from werkzeug.security import generate_password_hash
    
    app.logger.info(f"creating account for {email}")  
    passwd = generate_password_hash(password)
    salt = hashlib.sha1(str(random.random()).encode('utf8')).hexdigest()[:20]

    response = tbl_accounts_client.put_item(
        Item={
                "email": email,
                "password": passwd,
                "activation_key": str(salt),
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
    )

    return response
