import os
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Application, ApplicationStatusHistory, Company, Job, Resume, User

company_bp = Blueprint("company", __name__, url_prefix="/company")

APPLICATION_STATUSES = {"submitted", "accepted", "rejected"}


def company_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))

        user = db.session.get(User, user_id)
        if user is None:
            session.pop("user_id", None)
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))
        if not user.is_active or user.role != "company":
            abort(403)

        company = Company.query.filter_by(user_id=user.user_id).first()
        if company is None:
            abort(403)

        return view(company, user, *args, **kwargs)

    return wrapped_view


def owned_job_or_404(company, job_id):
    job = Job.query.filter_by(job_id=job_id, company_id=company.company_id).first()
    if job is None:
        abort(404)
    return job


def owned_application_or_404(company, application_id):
    application = (
        Application.query.join(Job)
        .filter(
            Application.application_id == application_id,
            Job.company_id == company.company_id,
        )
        .first()
    )
    if application is None:
        abort(404)
    return application


def effective_job_status(job):
    if job.status not in {"blocked", "closed"} and job.deadline and job.deadline < date.today():
        return "closed"
    return job.status


def validate_job_form(form):
    title = form.get("title", "").strip()
    content = form.get("content", "").strip()
    region = form.get("region", "").strip()
    industry = form.get("industry", "").strip()
    deadline_text = form.get("deadline", "").strip()
    errors = []

    if not title:
        errors.append("공고 제목을 입력해 주세요.")
    elif len(title) > 200:
        errors.append("공고 제목은 200자 이하여야 합니다.")
    if not content:
        errors.append("공고 내용을 입력해 주세요.")
    elif len(content) > 10000:
        errors.append("공고 내용은 10,000자 이하여야 합니다.")
    if len(region) > 80:
        errors.append("지역은 80자 이하여야 합니다.")
    if len(industry) > 80:
        errors.append("업종은 80자 이하여야 합니다.")

    deadline = None
    if deadline_text:
        try:
            deadline = datetime.strptime(deadline_text, "%Y-%m-%d").date()
        except ValueError:
            errors.append("마감일 형식이 올바르지 않습니다.")
        else:
            if deadline < date.today():
                errors.append("마감일은 오늘 이후로 선택해 주세요.")

    return {
        "title": title,
        "content": content,
        "region": region or None,
        "industry": industry or None,
        "deadline": deadline,
    }, errors


@company_bp.route("/")
@company_bp.route("/dashboard")
def dashboard_alias():
    return redirect(url_for("company.dashboard"))


@company_bp.route("/jobs")
@company_required
def dashboard(company, user):
    jobs = Job.query.filter_by(company_id=company.company_id).order_by(Job.created_at.desc()).all()
    applicant_counts = {
        job_id: count
        for job_id, count in (
            db.session.query(Application.job_id, db.func.count(Application.application_id))
            .join(Job)
            .filter(Job.company_id == company.company_id)
            .group_by(Application.job_id)
            .all()
        )
    }
    return render_template(
        "company/dashboard.html",
        company=company,
        jobs=jobs,
        applicant_counts=applicant_counts,
        effective_job_status=effective_job_status,
    )


@company_bp.route("/jobs/new", methods=["GET", "POST"])
@company_required
def job_new(company, user):
    if request.method == "POST":
        values, errors = validate_job_form(request.form)
        if not errors:
            job = Job(company_id=company.company_id, status="pending", **values)
            db.session.add(job)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                errors.append("공고를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            else:
                flash("공고가 등록되었습니다. 관리자 승인 후 공개됩니다.", "success")
                return redirect(url_for("company.dashboard"))
        for error in errors:
            flash(error, "error")

    return render_template("company/job_form.html", job=None, page_title="공고 등록")


@company_bp.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@company_required
def job_edit(company, user, job_id):
    job = owned_job_or_404(company, job_id)
    if job.status == "blocked":
        flash("차단된 공고는 기업에서 수정할 수 없습니다.", "error")
        return redirect(url_for("company.dashboard"))
    if effective_job_status(job) == "closed":
        flash("마감된 공고는 수정할 수 없습니다.", "error")
        return redirect(url_for("company.dashboard"))

    if request.method == "POST":
        values, errors = validate_job_form(request.form)
        if not errors:
            for field, value in values.items():
                setattr(job, field, value)
            if job.status == "approved":
                job.status = "pending"
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                errors.append("공고를 수정하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            else:
                flash("공고가 수정되었습니다.", "success")
                return redirect(url_for("company.dashboard"))
        for error in errors:
            flash(error, "error")

    return render_template("company/job_form.html", job=job, page_title="공고 수정")


@company_bp.post("/jobs/<int:job_id>/close")
@company_required
def job_close(company, user, job_id):
    job = owned_job_or_404(company, job_id)
    if job.status == "blocked":
        flash("차단된 공고는 기업에서 상태를 변경할 수 없습니다.", "error")
    elif effective_job_status(job) == "closed":
        flash("이미 마감된 공고입니다.", "error")
    else:
        job.status = "closed"
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("공고를 마감하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
        else:
            flash("공고가 마감되었습니다.", "success")
    return redirect(url_for("company.dashboard"))


@company_bp.route("/applicants")
@company_required
def applicants(company, user):
    selected_job_id = request.args.get("job_id", type=int)
    jobs = Job.query.filter_by(company_id=company.company_id).order_by(Job.created_at.desc()).all()
    query = Application.query.join(Job).filter(Job.company_id == company.company_id)
    if selected_job_id is not None:
        owned_job_or_404(company, selected_job_id)
        query = query.filter(Application.job_id == selected_job_id)
    applications = query.order_by(Application.applied_at.desc()).all()
    users = {
        applicant.user_id: applicant
        for applicant in User.query.filter(User.user_id.in_({item.user_id for item in applications})).all()
    } if applications else {}
    return render_template(
        "company/applicants.html",
        applications=applications,
        applicant_users=users,
        jobs=jobs,
        selected_job_id=selected_job_id,
    )


@company_bp.route("/applicants/<int:application_id>")
@company_required
def applicant_detail(company, user, application_id):
    application = owned_application_or_404(company, application_id)
    applicant = db.session.get(User, application.user_id)
    resume = db.session.get(Resume, application.resume_id) if application.resume_id else None
    return render_template(
        "company/applicant_detail.html",
        application=application,
        applicant=applicant,
        resume=resume,
    )


@company_bp.route("/applicants/<int:application_id>/resume")
@company_required
def applicant_resume(company, user, application_id):
    application = owned_application_or_404(company, application_id)
    resume = db.session.get(Resume, application.resume_id) if application.resume_id else None
    if not resume or not resume.file_path:
        abort(404)

    stored_filename = os.path.basename(resume.file_path)
    if stored_filename != resume.file_path:
        abort(404)

    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored_filename)
    if not os.path.isfile(file_path):
        flash("제출된 이력서 파일을 찾을 수 없습니다.", "error")
        return redirect(url_for("company.applicant_detail", application_id=application.application_id))

    extension = stored_filename.rsplit(".", 1)[-1].lower()
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        stored_filename,
        as_attachment=extension != "pdf",
        download_name=resume.original_filename or stored_filename,
        conditional=True,
    )


@company_bp.post("/applicants/<int:application_id>/status")
@company_required
def applicant_status(company, user, application_id):
    application = owned_application_or_404(company, application_id)
    new_status = request.form.get("status", "")
    if new_status not in APPLICATION_STATUSES:
        flash("변경할 수 없는 지원 상태입니다.", "error")
        return redirect(url_for("company.applicant_detail", application_id=application_id))
    if application.status == "cancelled":
        flash("취소된 지원서는 상태를 변경할 수 없습니다.", "error")
        return redirect(url_for("company.applicant_detail", application_id=application_id))
    if application.status == new_status:
        flash("현재 상태와 동일합니다.", "error")
        return redirect(url_for("company.applicant_detail", application_id=application_id))

    old_status = application.status
    application.status = new_status
    db.session.add(
        ApplicationStatusHistory(
            application_id=application.application_id,
            old_status=old_status,
            new_status=new_status,
            changed_by=user.user_id,
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("지원 상태를 변경하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error")
    else:
        flash("지원 상태가 변경되었습니다.", "success")
    return redirect(url_for("company.applicant_detail", application_id=application_id))
