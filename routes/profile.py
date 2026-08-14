import os
import uuid
import unicodedata

from flask import Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from input_validation import is_valid_email, password_policy_error, validate_display_text
from models import Application, Job, Resume, Scrap, User
from resume_validation import is_valid_resume_file, resume_filename_details
from session_security import refresh_user_session

profile_bp = Blueprint("profile", __name__)
MAX_PROFILE_IMAGE_URL_LENGTH = 1000


def _render_mypage(**context):
    response = make_response(render_template("profile/mypage.html", **context))
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'nonce-{g.csp_nonce}'; "
        "style-src 'self'; img-src 'self' data: https:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
    )
    return response


def _profile_image_url_error(value):
    if len(value) > MAX_PROFILE_IMAGE_URL_LENGTH:
        return "프로필 이미지 URL은 1,000자 이하여야 합니다."
    if any(unicodedata.category(character).startswith("C") for character in value):
        return "프로필 이미지 URL에 사용할 수 없는 문자가 포함되어 있습니다."
    lowered = value.casefold()
    if "<" in value or ">" in value or "</style" in lowered or "<script" in lowered:
        return "프로필 이미지 URL에 HTML 문법을 사용할 수 없습니다."
    if "javascript:" in lowered:
        return "프로필 이미지 URL에 JavaScript 주소를 사용할 수 없습니다."
    return None


def _resume_management_redirect():
    endpoint = "profile.my_resumes" if request.form.get("return_to") == "resumes" else "profile.mypage"
    return redirect(url_for(endpoint))


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
    filename_details = resume_filename_details(
        uploaded_file.filename if uploaded_file and uploaded_file.filename else ""
    )
    if not filename_details:
        flash("등록할 이력서 파일을 선택해 주세요.", "error")
        return _resume_management_redirect()
    original_name, extension, stored_extension, educational_html = filename_details
    if not is_valid_resume_file(uploaded_file, extension, educational_html):
        flash("파일 형식과 내용이 일치하는 이력서만 등록할 수 있습니다.", "error")
        return _resume_management_redirect()

    stored_filename = f"{uuid.uuid4().hex}.{stored_extension}"
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
    return _resume_management_redirect()


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


@profile_bp.post("/mypage/resumes/<int:resume_id>/delete")
def resume_delete(resume_id):
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role != "jobseeker":
        abort(403)

    resume = Resume.query.filter_by(resume_id=resume_id, user_id=user.user_id).first()
    if resume is None:
        abort(404)

    if Application.query.filter_by(resume_id=resume.resume_id).first():
        resume.is_deleted = True
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("이력서를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
        else:
            flash("내 이력서에서 삭제했습니다. 기존 지원서의 제출본은 보존됩니다.", "success")
        return _resume_management_redirect()

    stored_filename = os.path.basename(resume.file_path) if resume.file_path else None
    can_delete_file = bool(stored_filename and stored_filename == resume.file_path)
    db.session.delete(resume)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("이력서를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
    else:
        if can_delete_file:
            file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError:
                current_app.logger.exception("Failed to remove resume file: %s", stored_filename)
        flash("이력서가 삭제되었습니다.", "success")

    return _resume_management_redirect()


@profile_bp.route("/mypage/resumes")
def my_resumes():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role != "jobseeker":
        abort(403)

    resumes = (
        Resume.query.filter_by(user_id=user.user_id, is_deleted=False)
        .order_by(Resume.uploaded_at.desc())
        .all()
    )
    return render_template("profile/resumes.html", resumes=resumes)


@profile_bp.post("/mypage/profile-image")
def update_profile_image():
    user = _require_active_user()
    if not user:
        return redirect(url_for("auth.login"))
    if user.role == "admin":
        abort(403)

    action = request.form.get("action", "save")
    profile_image_url = (request.form.get("profile_image_url") or "").strip()
    if action == "delete":
        profile_image_url = ""
    elif action != "save":
        abort(400)

    error = _profile_image_url_error(profile_image_url)
    if error:
        flash(error, "error")
        return redirect(url_for("profile.mypage"))

    user.profile_image_url = profile_image_url or None
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("프로필 이미지를 변경하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
    else:
        flash("프로필 이미지가 삭제되었습니다." if not profile_image_url else "프로필 이미지가 저장되었습니다.", "success")
    return redirect(url_for("profile.mypage"))


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

        name_error = validate_display_text(name, 80, "이름")
        if name_error:
            flash(name_error, "error")
        elif not is_valid_email(email):
            flash("올바른 이메일 주소를 입력해 주세요.", "error")
        elif User.query.filter(User.email == email, User.user_id != user.user_id).first():
            flash("이미 사용 중인 이메일입니다.", "error")
        elif email != user.email and not check_password_hash(
            user.password_hash, request.form.get("current_password") or ""
        ):
            flash("이메일을 변경하려면 현재 비밀번호를 확인해 주세요.", "error")
        else:
            user.name = name
            user.email = email
            if user.role == "company" and user.company:
                company_name = (request.form.get("company_name") or "").strip()
                company_name_error = validate_display_text(company_name, 120, "회사명")
                if company_name_error:
                    flash(company_name_error, "error")
                    return redirect(url_for("profile.mypage"))
                user.company.company_name = company_name
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("이미 사용 중인 이메일입니다.", "error")
            else:
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
        return _render_mypage(user=user, stats=stats, jobs=jobs)

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
        Resume.query.filter_by(user_id=user.user_id, is_deleted=False)
        .order_by(Resume.uploaded_at.desc())
        .limit(5)
        .all()
    )
    stats = {
        "applications": Application.query.filter_by(user_id=user.user_id).count(),
        "scraps": Scrap.query.filter_by(user_id=user.user_id).count(),
        "resumes": Resume.query.filter_by(user_id=user.user_id, is_deleted=False).count(),
    }
    return _render_mypage(
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
    elif password_policy_error(new_password, "새 비밀번호"):
        flash(password_policy_error(new_password, "새 비밀번호"), "error")
    elif new_password != new_password_confirm:
        flash("새 비밀번호가 일치하지 않습니다.", "error")
    elif check_password_hash(user.password_hash, new_password):
        flash("현재 비밀번호와 다른 비밀번호를 입력해 주세요.", "error")
    else:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        refresh_user_session(user)
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

    removable_files = []
    if user.role == "jobseeker":
        resumes = Resume.query.filter_by(user_id=user.user_id).all()
        used_resume_ids = {
            resume_id
            for (resume_id,) in db.session.query(Application.resume_id)
            .filter(
                Application.user_id == user.user_id,
                Application.resume_id.is_not(None),
            )
            .all()
        }
        for resume in resumes:
            if resume.resume_id in used_resume_ids:
                resume.is_deleted = True
                continue
            stored_filename = os.path.basename(resume.file_path) if resume.file_path else None
            if stored_filename and stored_filename == resume.file_path:
                removable_files.append(stored_filename)
            db.session.delete(resume)

    user.email = f"withdrawn-{user.user_id}@1itda.local"
    user.name = None
    user.password_hash = generate_password_hash(uuid.uuid4().hex)
    user.is_active = False
    db.session.commit()

    for stored_filename in removable_files:
        file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            current_app.logger.exception(
                "Failed to remove withdrawn user's unused resume: %s", stored_filename
            )

    session.clear()
    flash("회원 탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.", "success")
    return redirect(url_for("index"))
