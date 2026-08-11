import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import Company, LoginThrottle, User

auth_bp = Blueprint("auth", __name__)
LOGIN_WINDOW = timedelta(minutes=15)
USER_LOCK_DURATION = timedelta(minutes=15)
ADMIN_LOCK_DURATION = timedelta(minutes=30)
USER_MAX_ATTEMPTS = 5
ADMIN_MAX_ATTEMPTS = 3
GENERIC_LOGIN_ERROR = "이메일 또는 비밀번호가 올바르지 않거나 로그인이 잠시 제한되었습니다."
GENERIC_RESET_MESSAGE = "가입된 계정인 경우 비밀번호 재설정 메일이 발송됩니다."


def _throttle_key(email):
    remote_address = request.remote_addr or "unknown"
    return hashlib.sha256(f"{email}|{remote_address}".encode()).hexdigest()


def _get_throttle(email):
    return LoginThrottle.query.filter_by(throttle_key=_throttle_key(email)).first()


def _is_login_locked(throttle, now):
    return bool(throttle and throttle.locked_until and throttle.locked_until > now)


def _record_login_failure(email, is_admin, now):
    throttle = _get_throttle(email)
    if throttle is None:
        throttle = LoginThrottle(
            throttle_key=_throttle_key(email),
            failed_attempts=0,
            window_started_at=now,
        )
        db.session.add(throttle)
    elif now - throttle.window_started_at >= LOGIN_WINDOW:
        throttle.failed_attempts = 0
        throttle.window_started_at = now
        throttle.locked_until = None

    throttle.failed_attempts += 1
    max_attempts = ADMIN_MAX_ATTEMPTS if is_admin else USER_MAX_ATTEMPTS
    if throttle.failed_attempts >= max_attempts:
        throttle.locked_until = now + (ADMIN_LOCK_DURATION if is_admin else USER_LOCK_DURATION)
    db.session.commit()


def _clear_login_failures(email):
    throttle = _get_throttle(email)
    if throttle:
        db.session.delete(throttle)
        db.session.commit()


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def _send_reset_email(user, reset_url):
    host = current_app.config.get("MAIL_HOST")
    if not host:
        current_app.logger.info("Password reset URL for %s: %s", user.email, reset_url)
        return

    message = EmailMessage()
    message["Subject"] = "[1ITDA] 비밀번호 재설정"
    message["From"] = current_app.config["MAIL_FROM"]
    message["To"] = user.email
    message.set_content(
        "아래 링크에서 30분 이내에 비밀번호를 재설정해 주세요.\n\n"
        f"{reset_url}\n\n요청하지 않았다면 이 메일을 무시해 주세요."
    )
    with smtplib.SMTP(host, current_app.config["MAIL_PORT"], timeout=10) as smtp:
        if current_app.config.get("MAIL_USE_TLS"):
            smtp.starttls()
        username = current_app.config.get("MAIL_USERNAME")
        if username:
            smtp.login(username, current_app.config.get("MAIL_PASSWORD") or "")
        smtp.send_message(message)


@auth_bp.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "GET":
        return render_template("auth/join.html")

    role = request.form.get("role")
    name = (request.form.get("name") or "").strip()
    company_name = (request.form.get("company_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    password_confirm = request.form.get("password_confirm") or ""
    privacy_agreed = request.form.get("privacy_agreed") == "1"

    errors = []
    if role not in ("jobseeker", "company"):
        errors.append("회원 유형을 선택해 주세요.")
    if not name:
        errors.append("이름을 입력해 주세요.")
    if role == "company" and not company_name:
        errors.append("회사명을 입력해 주세요.")
    if not email:
        errors.append("이메일을 입력해 주세요.")
    if len(password) < 8:
        errors.append("비밀번호는 8자 이상이어야 합니다.")
    if password != password_confirm:
        errors.append("비밀번호가 일치하지 않습니다.")
    if not privacy_agreed:
        errors.append("개인정보 수집 및 이용에 동의해 주세요.")
    if email and User.query.filter_by(email=email).first():
        errors.append("이미 가입된 이메일입니다.")

    if errors:
        for message in errors:
            flash(message, "error")
        return render_template("auth/join.html"), 400

    user = User(
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
        name=name,
    )
    db.session.add(user)
    db.session.flush()

    if role == "company":
        db.session.add(Company(user_id=user.user_id, company_name=company_name))

    db.session.commit()

    session.clear()
    session["user_id"] = user.user_id
    session["role"] = user.role
    flash("회원가입이 완료되었습니다.", "success")
    return redirect(url_for("index"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    now = datetime.utcnow()
    throttle = _get_throttle(email)

    if _is_login_locked(throttle, now):
        flash(GENERIC_LOGIN_ERROR, "error")
        return render_template("auth/login.html"), 401

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password_hash, password):
        _record_login_failure(email, bool(user and user.role == "admin"), now)
        flash(GENERIC_LOGIN_ERROR, "error")
        return render_template("auth/login.html"), 401

    if not user.is_active:
        flash("이용이 제한된 계정입니다.", "error")
        return render_template("auth/login.html"), 403

    _clear_login_failures(email)
    session.clear()
    session["user_id"] = user.user_id
    session["role"] = user.role
    return redirect(url_for("index"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email, is_active=True).first() if email else None
        if user:
            user.password_reset_nonce = secrets.token_urlsafe(32)
            db.session.commit()
            token = _reset_serializer().dumps(
                {"user_id": user.user_id, "nonce": user.password_reset_nonce}
            )
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            try:
                _send_reset_email(user, reset_url)
            except (OSError, smtplib.SMTPException):
                current_app.logger.exception("Failed to send password reset email")

        flash(GENERIC_RESET_MESSAGE, "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        payload = _reset_serializer().loads(
            token,
            max_age=current_app.config["PASSWORD_RESET_MAX_AGE"],
        )
    except (BadSignature, SignatureExpired):
        flash("비밀번호 재설정 링크가 만료되었거나 올바르지 않습니다.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = db.session.get(User, payload.get("user_id"))
    if (
        not user
        or not user.is_active
        or not user.password_reset_nonce
        or not secrets.compare_digest(user.password_reset_nonce, payload.get("nonce") or "")
    ):
        flash("비밀번호 재설정 링크가 만료되었거나 이미 사용되었습니다.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        new_password_confirm = request.form.get("new_password_confirm") or ""
        if len(new_password) < 8:
            flash("새 비밀번호는 8자 이상이어야 합니다.", "error")
        elif new_password != new_password_confirm:
            flash("새 비밀번호가 일치하지 않습니다.", "error")
        elif check_password_hash(user.password_hash, new_password):
            flash("기존 비밀번호와 다른 비밀번호를 입력해 주세요.", "error")
        else:
            user.password_hash = generate_password_hash(new_password)
            user.password_reset_nonce = secrets.token_urlsafe(32)
            db.session.commit()
            session.clear()
            flash("비밀번호가 변경되었습니다. 새 비밀번호로 로그인해 주세요.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
