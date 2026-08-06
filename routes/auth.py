from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from models import Company, User

auth_bp = Blueprint("auth", __name__)


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

    user = User.query.filter_by(email=email).first()

    if not user or not check_password_hash(user.password_hash, password):
        flash("이메일 또는 비밀번호가 올바르지 않습니다.", "error")
        return render_template("auth/login.html"), 401

    if not user.is_active:
        flash("이용이 제한된 계정입니다.", "error")
        return render_template("auth/login.html"), 403

    session.clear()
    session["user_id"] = user.user_id
    session["role"] = user.role
    return redirect(url_for("index"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
