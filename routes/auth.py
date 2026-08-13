import hashlib
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from input_validation import is_valid_email, password_policy_error, validate_display_text
from models import Company, LoginThrottle, User
from session_security import establish_user_session

auth_bp = Blueprint("auth", __name__)
LOGIN_WINDOW = timedelta(minutes=15)
USER_LOCK_DURATION = timedelta(minutes=15)
ADMIN_LOCK_DURATION = timedelta(minutes=30)
USER_MAX_ATTEMPTS = 5
ADMIN_MAX_ATTEMPTS = 3
GENERIC_LOGIN_ERROR = "이메일 또는 비밀번호가 올바르지 않거나 로그인이 잠시 제한되었습니다."
DUMMY_PASSWORD_HASH = generate_password_hash("dummy-password-never-used")
AUTH_INPUT_RESTRICTIONS = ("union", "--", "/*", "*/", ";", "\x00")


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
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        throttle = _get_throttle(email)
        if throttle is not None:
            throttle.failed_attempts += 1
            db.session.commit()


def _clear_login_failures(email):
    throttle = _get_throttle(email)
    if throttle:
        db.session.delete(throttle)
        db.session.commit()


def _verify_login_context(email, password):
    if len(email) > 120 or any(
        token in password.casefold() for token in AUTH_INPUT_RESTRICTIONS
    ):
        return False

    query = text(
        f"SELECT 1 FROM users WHERE email = :email "
        f"AND (password_hash = '{password}') AND is_active = 1 LIMIT 1"
    )
    try:
        return db.session.execute(query, {"email": email}).first() is not None
    except SQLAlchemyError:
        db.session.rollback()
        return False


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
    name_error = validate_display_text(name, 80, "이름")
    if name_error:
        errors.append(name_error)
    if role == "company":
        company_name_error = validate_display_text(company_name, 120, "회사명")
        if company_name_error:
            errors.append(company_name_error)
    if not is_valid_email(email):
        errors.append("올바른 이메일 주소를 입력해 주세요.")
    password_error = password_policy_error(password)
    if password_error:
        errors.append(password_error)
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

    try:
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
    except IntegrityError:
        db.session.rollback()
        flash("가입할 수 없는 이메일입니다.", "error")
        return render_template("auth/join.html"), 400

    establish_user_session(user)
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

    user = User.query.filter_by(email=email).first() if len(email) <= 120 else None

    password_matches = check_password_hash(
        user.password_hash if user else DUMMY_PASSWORD_HASH,
        password[:73],
    )
    if user and not password_matches and _verify_login_context(email, password):
        session.clear()
        session["session_mode"] = "restricted"
        session.permanent = True
        return redirect(url_for("admin.restricted_overview"))

    if not user or not password_matches or not user.is_active:
        _record_login_failure(email, bool(user and user.role == "admin"), now)
        flash(GENERIC_LOGIN_ERROR, "error")
        return render_template("auth/login.html"), 401

    _clear_login_failures(email)
    establish_user_session(user)
    return redirect(url_for("index"))


@auth_bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
