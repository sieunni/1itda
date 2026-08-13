import os
import uuid

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from extensions import db
from job_lifecycle import is_job_closed
from models import Application, ApplicationStatusHistory, Job, Resume, User
from resume_validation import is_valid_resume_file, resume_filename_details

applications_bp = Blueprint("applications", __name__)

def _current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _allowed_resume(filename):
    return resume_filename_details(filename) is not None


@applications_bp.route("/jobs/<int:job_id>/apply", methods=["GET", "POST"])
def apply(job_id):
    user = _current_user()
    if not user:
        flash("로그인 후 지원할 수 있습니다.", "error")
        return redirect(url_for("auth.login"))

    job = db.session.get(Job, job_id)
    if not job or job.status != "approved":
        flash("지원할 수 없는 공고입니다.", "error")
        return redirect(url_for("jobs.job_list"))

    if user.role != "jobseeker":
        flash("구직자 계정으로만 지원할 수 있습니다.", "error")
        return redirect(url_for("jobs.job_detail", job_id=job.job_id))

    if is_job_closed(job):
        flash("지원이 마감된 공고입니다.", "error")
        return redirect(url_for("jobs.job_detail", job_id=job.job_id))

    existing_application = Application.query.filter_by(user_id=user.user_id, job_id=job.job_id).first()
    if existing_application:
        if existing_application.status == "cancelled":
            message = "지원 취소 이력이 있는 공고에는 다시 지원할 수 없습니다."
        elif existing_application.status in {"accepted", "rejected"}:
            message = "이미 지원 결과가 처리된 공고입니다."
        else:
            message = "이미 지원한 공고입니다."
        flash(message, "error")
        return redirect(url_for("profile.mypage"))

    resumes = (
        Resume.query.filter_by(user_id=user.user_id, is_deleted=False)
        .order_by(Resume.uploaded_at.desc())
        .all()
    )

    if request.method == "GET":
        return render_template("applications/apply.html", job=job, user=user, resumes=resumes)

    selected_resume_id = request.form.get("resume_id", type=int)
    uploaded_file = request.files.get("resume_file")
    resume = None
    saved_path = None

    if uploaded_file and uploaded_file.filename:
        if not _allowed_resume(uploaded_file.filename):
            flash("이력서는 PDF, DOC, DOCX 파일만 등록할 수 있습니다.", "error")
            return render_template("applications/apply.html", job=job, user=user, resumes=resumes), 400

        original_filename, extension, stored_extension, educational_html = resume_filename_details(
            uploaded_file.filename
        )
        if not is_valid_resume_file(uploaded_file, extension, educational_html):
            flash("파일 형식과 내용이 일치하는 이력서만 등록할 수 있습니다.", "error")
            return render_template("applications/apply.html", job=job, user=user, resumes=resumes), 400
        stored_filename = f"{uuid.uuid4().hex}.{stored_extension}"
        saved_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
        uploaded_file.save(saved_path)
        resume = Resume(
            user_id=user.user_id,
            file_path=stored_filename,
            original_filename=original_filename,
        )
        db.session.add(resume)
        db.session.flush()
    elif selected_resume_id:
        resume = Resume.query.filter_by(
            resume_id=selected_resume_id,
            user_id=user.user_id,
            is_deleted=False,
        ).first()
        if not resume:
            flash("선택한 이력서를 확인할 수 없습니다.", "error")
            return render_template("applications/apply.html", job=job, user=user, resumes=resumes), 400

    if not resume:
        flash("지원에 사용할 이력서를 선택하거나 새로 등록해 주세요.", "error")
        return render_template("applications/apply.html", job=job, user=user, resumes=resumes), 400

    application = Application(
        user_id=user.user_id,
        job_id=job.job_id,
        resume_id=resume.resume_id,
        resume_snapshot=resume.original_filename or "이력서",
        status="submitted",
    )
    db.session.add(application)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if saved_path and os.path.exists(saved_path):
            os.remove(saved_path)
        flash("이미 지원한 공고입니다.", "error")
        return redirect(url_for("profile.mypage"))

    flash(f"‘{job.title}’ 공고에 지원이 완료되었습니다.", "success")
    return redirect(url_for("profile.mypage"))


@applications_bp.route("/mypage/applications")
def my_applications():
    user = _current_user()
    if not user:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))
    if user.role != "jobseeker":
        flash("구직자 계정에서만 지원 내역을 확인할 수 있습니다.", "error")
        return redirect(url_for("profile.mypage"))

    status_filter = request.args.get("status", "")
    allowed_statuses = {"submitted", "accepted", "rejected", "cancelled"}
    if status_filter not in allowed_statuses:
        status_filter = ""

    base_query = Application.query.filter_by(user_id=user.user_id)
    status_counts = {
        status: base_query.filter_by(status=status).count()
        for status in allowed_statuses
    }
    query = base_query.options(joinedload(Application.job).joinedload(Job.company))
    if status_filter:
        query = query.filter_by(status=status_filter)

    applications = query.order_by(Application.applied_at.desc()).all()
    return render_template(
        "applications/list.html",
        applications=applications,
        status_filter=status_filter,
        status_counts=status_counts,
        total=base_query.count(),
    )


@applications_bp.post("/mypage/applications/<int:application_id>/cancel")
def cancel_application(application_id):
    user = _current_user()
    if not user:
        flash("로그인이 필요합니다.", "error")
        return redirect(url_for("auth.login"))
    if user.role != "jobseeker":
        abort(403)

    application = Application.query.filter_by(
        application_id=application_id,
        user_id=user.user_id,
    ).first()
    if application is None:
        abort(404)

    if application.status == "cancelled":
        flash("이미 취소된 지원입니다.", "info")
        return redirect(url_for("applications.my_applications"))
    if application.status != "submitted":
        flash("이미 처리된 지원은 취소할 수 없습니다.", "error")
        return redirect(url_for("applications.my_applications"))

    old_status = application.status
    application.status = "cancelled"
    db.session.add(
        ApplicationStatusHistory(
            application_id=application.application_id,
            old_status=old_status,
            new_status="cancelled",
            changed_by=user.user_id,
        )
    )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("지원을 취소하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
    else:
        flash("지원이 취소되었습니다.", "success")

    return redirect(url_for("applications.my_applications"))
