import hashlib
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
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


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
