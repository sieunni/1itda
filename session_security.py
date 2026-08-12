import hashlib
import hmac

from flask import current_app, session


def auth_fingerprint(user):
    secret_key = current_app.config["SECRET_KEY"]
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    return hmac.new(
        secret_key,
        user.password_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def establish_user_session(user):
    session.clear()
    session["user_id"] = user.user_id
    session["role"] = user.role
    session["auth_fingerprint"] = auth_fingerprint(user)


def refresh_user_session(user):
    session["auth_fingerprint"] = auth_fingerprint(user)
