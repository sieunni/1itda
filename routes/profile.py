import os
import secrets
import uuid

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from extensions import db
from models import Application, Job, Resume, Scrap, User

profile_bp = Blueprint("profile", __name__)
ALLOWED_RESUME_EXTENSIONS = {"pdf", "doc", "docx"}


def _require_active_user():
    user_id = session.get("user_id")
    if not user_id:
        flash("로그인 후 이용해 주세요.", "error")
        return None

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        session.clear()
        flash("사용자 정보를 확인할 수 없습니다.", "error")
        return None

    return user


@profile_bp.post("/mypage/resumes/upload")
def resume_upload():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    if not user:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))
    if user.role != "jobseeker":
        abort(403)

    uploaded_file = request.files.get("resume_file")
    original_name = secure_filename(uploaded_file.filename) if uploaded_file and uploaded_file.filename else ""
    if not original_name or "." not in original_name:
        flash("등록할 이력서 파일을 선택해 주세요.", "error")
        return redirect(url_for("profile.mypage"))

    extension = original_name.rsplit(".", 1)[1].lower()
    if extension not in ALLOWED_RESUME_EXTENSIONS:
        flash("이력서는 PDF, DOC, DOCX 파일만 등록할 수 있습니다.", "error")
        return redirect(url_for("profile.mypage"))

    stored_filename = f"{uuid.uuid4().hex}.{extension}"
    saved_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
    uploaded_file.save(saved_path)
    db.session.add(
        Resume(
            user_id=user.user_id,
            file_path=stored_filename,
            original_filename=original_name,
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        if os.path.isfile(saved_path):
            os.remove(saved_path)
        flash("이력서를 등록하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
    else:
        flash("새 이력서가 등록되었습니다.", "success")
    return redirect(url_for("profile.mypage"))


@profile_bp.route("/mypage/resumes/<int:resume_id>/preview")
def resume_preview(resume_id):
    user_id = session.get("user_id")
    if not user_id:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))

    resume = Resume.query.filter_by(resume_id=resume_id, user_id=user_id).first()
    if not resume or not resume.file_path:
        abort(404)

    stored_filename = os.path.basename(resume.file_path)
    if stored_filename != resume.file_path:
        abort(404)

    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
    if not os.path.isfile(file_path):
        flash("이력서 파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("profile.mypage"))

    extension = stored_filename.rsplit(".", 1)[-1].lower()
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        stored_filename,
        as_attachment=extension != "pdf",
        download_name=resume.original_filename or stored_filename,
        conditional=True,
    )


@profile_bp.route("/mypage", methods=["GET", "POST"])
def mypage():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))

    if user.role == "admin":
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()

        if not name or not email:
            flash("이름과 이메일을 모두 입력해 주세요.", "error")
        elif User.query.filter(User.email == email, User.user_id != user.user_id).first():
            flash("이미 사용 중인 이메일입니다.", "error")
        else:
            user.name = name
            user.email = email
            if user.role == "company" and user.company:
                company_name = (request.form.get("company_name") or "").strip()
                if not company_name:
                    flash("회사명을 입력해 주세요.", "error")
                    return redirect(url_for("profile.mypage"))
                user.company.company_name = company_name
            db.session.commit()
            flash("회원 정보가 수정되었습니다.", "success")
        return redirect(url_for("profile.mypage"))

    if user.role == "company":
        jobs = (
            Job.query.filter_by(company_id=user.company.company_id)
            .order_by(Job.created_at.desc())
            .limit(5)
            .all()
            if user.company
            else []
        )
        stats = {
            "jobs": Job.query.filter_by(company_id=user.company.company_id).count() if user.company else 0,
            "open_jobs": Job.query.filter_by(company_id=user.company.company_id, status="approved").count()
            if user.company
            else 0,
            "applicants": Application.query.join(Job).filter(Job.company_id == user.company.company_id).count()
            if user.company
            else 0,
        }
        return render_template("profile/mypage.html", user=user, stats=stats, jobs=jobs)

    applications = (
        Application.query.filter_by(user_id=user.user_id)
        .order_by(Application.applied_at.desc())
        .limit(5)
        .all()
    )
    scraps = (
        Scrap.query.filter_by(user_id=user.user_id)
        .order_by(Scrap.created_at.desc())
        .limit(5)
        .all()
    )
    resumes = (
        Resume.query.filter_by(user_id=user.user_id)
        .order_by(Resume.uploaded_at.desc())
        .limit(5)
        .all()
    )
    stats = {
        "applications": Application.query.filter_by(user_id=user.user_id).count(),
        "scraps": Scrap.query.filter_by(user_id=user.user_id).count(),
        "resumes": Resume.query.filter_by(user_id=user.user_id).count(),
    }
    return render_template(
        "profile/mypage.html",
        user=user,
        stats=stats,
        applications=applications,
        scraps=scraps,
        resumes=resumes,
    )


@profile_bp.route("/mypage/scraps")
def my_scraps():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))

    if user.role != "jobseeker":
        flash("구직자 계정에서만 스크랩 목록을 확인할 수 있습니다.", "error")
        return redirect(url_for("profile.mypage"))

    scraps = (
        Scrap.query.filter_by(user_id=user.user_id)
        .options(joinedload(Scrap.job).joinedload(Job.company))
        .order_by(Scrap.created_at.desc())
        .all()
    )
    return render_template("profile/scraps.html", scraps=scraps)


@profile_bp.post("/mypage/password")
def change_password():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role == "admin":
        abort(403)

    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    new_password_confirm = request.form.get("new_password_confirm") or ""

    if not check_password_hash(user.password_hash, current_password):
        flash("현재 비밀번호가 올바르지 않습니다.", "error")
    elif len(new_password) < 8:
        flash("새 비밀번호는 8자 이상이어야 합니다.", "error")
    elif new_password != new_password_confirm:
        flash("새 비밀번호가 일치하지 않습니다.", "error")
    elif check_password_hash(user.password_hash, new_password):
        flash("현재 비밀번호와 다른 비밀번호를 입력해 주세요.", "error")
    else:
        user.password_hash = generate_password_hash(new_password)
        user.password_reset_nonce = secrets.token_urlsafe(32)
        db.session.commit()
        flash("비밀번호가 변경되었습니다.", "success")

    return redirect(url_for("profile.mypage"))


@profile_bp.route("/mypage/withdraw", methods=["GET", "POST"])
def withdraw():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role == "admin":
        abort(403)

    if request.method == "GET":
        return render_template("profile/withdraw.html")

    password = request.form.get("password") or ""
    if not check_password_hash(user.password_hash, password):
        flash("비밀번호가 올바르지 않습니다.", "error")
        return render_template("profile/withdraw.html"), 400

    user.email = f"withdrawn-{user.user_id}@1itda.local"
    user.name = None
    user.password_hash = generate_password_hash(uuid.uuid4().hex)
    user.is_active = False
    db.session.commit()

    session.clear()
    flash("회원 탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.", "success")
    return redirect(url_for("index"))
