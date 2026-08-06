import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for

from extensions import db
from models import Application, Job, Resume, Scrap, User

profile_bp = Blueprint("profile", __name__)


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
    user_id = session.get("user_id")
    if not user_id:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        session.clear()
        flash("사용자 정보를 확인할 수 없습니다.", "error")
        return redirect(url_for("auth.login"))

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
