import hashlib
import hmac
import time

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
    now = int(time.time())
    session.clear()
    session["user_id"] = user.user_id
    session["auth_fingerprint"] = auth_fingerprint(user)
    session["issued_at"] = now
    session["last_activity"] = now
    session.permanent = True


def refresh_user_session(user):
    session["auth_fingerprint"] = auth_fingerprint(user)


def is_session_fresh(user):
    now = int(time.time())
    try:
        issued_at = int(session.get("issued_at"))
        last_activity = int(session.get("last_activity"))
    except (TypeError, ValueError):
        return False

    idle_limit = current_app.config[
        "ADMIN_SESSION_IDLE_SECONDS" if user.role == "admin" else "SESSION_IDLE_SECONDS"
    ]
    if now - issued_at > current_app.config["SESSION_ABSOLUTE_SECONDS"]:
        return False
    if now - last_activity > idle_limit:
        return False

    if now - last_activity >= 60:
        session["last_activity"] = now
    return True
