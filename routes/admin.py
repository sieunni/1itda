from functools import wraps
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import case
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    REPORT_REASON_LABELS,
    REVIEW_REPORT_REASON_LABELS,
    AdminActionLog,
    Company,
    Job,
    Report,
    Review,
    ReviewReport,
    User,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

JOB_STATUS_LABELS = {"pending": "승인 대기", "approved": "공개 중", "blocked": "차단", "closed": "마감"}
REPORT_STATUS_LABELS = {"pending": "미처리", "reviewed": "확인 완료", "rejected": "반려"}
REVIEW_REPORT_STATUS_LABELS = {
    "": "전체",
    "pending": "처리 대기",
    "dismissed": "기각",
    "hidden": "숨김",
}


def admin_required(view):
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
        if not user.is_active or user.role != "admin":
            abort(403)

        return view(user, *args, **kwargs)

    return wrapped_view


def log_action(admin, action_type, target_type, target_id):
    db.session.add(
        AdminActionLog(
            admin_id=admin.user_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
        )
    )


def _commit(success_message, error_message):
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash(error_message, "error")
        return False
    flash(success_message, "success")
    return True


@admin_bp.route("/")
@admin_required
def dashboard(admin):
    stats = {
        "jobseeker_count": User.query.filter_by(role="jobseeker").count(),
        "company_count": User.query.filter_by(role="company").count(),
        "pending_jobs": Job.query.filter_by(status="pending").count(),
        "approved_jobs": Job.query.filter_by(status="approved").count(),
        "blocked_jobs": Job.query.filter_by(status="blocked").count(),
        "pending_reports": (
            Report.query.filter_by(status="pending").count()
            + ReviewReport.query.filter_by(status="pending").count()
        ),
    }
    recent_jobs = Job.query.order_by(Job.created_at.desc()).limit(5).all()
    recent_reports = Report.query.order_by(Report.created_at.desc()).limit(5).all()
    report_job_ids = {
        report.target_id for report in recent_reports if report.target_type == "job"
    }
    report_jobs = {
        job.job_id: job
        for job in Job.query.filter(Job.job_id.in_(report_job_ids)).all()
    }
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_jobs=recent_jobs,
        recent_reports=recent_reports,
        report_jobs=report_jobs,
        job_status_labels=JOB_STATUS_LABELS,
        report_status_labels=REPORT_STATUS_LABELS,
        reason_labels=REPORT_REASON_LABELS,
    )


@admin_bp.route("/users")
@admin_required
def users(admin):
    role_filter = request.args.get("role", "")
    query = User.query.filter(User.role != "admin")
    if role_filter in ("jobseeker", "company"):
        query = query.filter_by(role=role_filter)
    member_list = query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=member_list, role_filter=role_filter)


@admin_bp.post("/users/<int:user_id>/toggle")
@admin_required
def toggle_user(admin, user_id):
    target = db.session.get(User, user_id)
    if target is None or target.role == "admin":
        abort(404)

    target.is_active = not target.is_active
    log_action(admin, "unblock" if target.is_active else "block", "user", target.user_id)
    _commit(
        "회원 차단을 해제했습니다." if target.is_active else "회원을 차단했습니다.",
        "회원 상태를 변경하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    )
    return redirect(url_for("admin.users", role=request.args.get("role", "")))


@admin_bp.route("/jobs")
@admin_required
def jobs(admin):
    status_filter = request.args.get("status", "")
    query = Job.query
    if status_filter in JOB_STATUS_LABELS:
        query = query.filter_by(status=status_filter)
    job_list = query.order_by(Job.created_at.desc()).all()
    return render_template(
        "admin/jobs.html",
        jobs=job_list,
        status_filter=status_filter,
        status_labels=JOB_STATUS_LABELS,
    )


@admin_bp.post("/jobs/<int:job_id>/approve")
@admin_required
def approve_job(admin, job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    job.status = "approved"
    log_action(admin, "approve", "job", job.job_id)
    _commit("공고를 승인했습니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.jobs", status=request.args.get("status", "")))


@admin_bp.post("/jobs/<int:job_id>/block")
@admin_required
def block_job(admin, job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    job.status = "blocked"
    log_action(admin, "block", "job", job.job_id)
    _commit("공고를 차단했습니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.jobs", status=request.args.get("status", "")))


@admin_bp.post("/jobs/<int:job_id>/unblock")
@admin_required
def unblock_job(admin, job_id):
    job = db.session.get(Job, job_id)
    if job is None:
        abort(404)
    job.status = "pending"
    log_action(admin, "unblock", "job", job.job_id)
    _commit("차단을 해제했습니다. 재승인이 필요합니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.jobs", status=request.args.get("status", "")))


@admin_bp.route("/reports")
@admin_required
def reports(admin):
    status_filter = request.args.get("status", "pending")
    allowed_filters = set(REVIEW_REPORT_STATUS_LABELS) | set(REPORT_STATUS_LABELS)
    if status_filter not in allowed_filters:
        status_filter = "pending"

    review_report_query = ReviewReport.query.options(
        joinedload(ReviewReport.review).joinedload(Review.company),
        joinedload(ReviewReport.review).joinedload(Review.author),
        joinedload(ReviewReport.reporter),
    )
    if status_filter in ("pending", "dismissed", "hidden"):
        review_report_query = review_report_query.filter_by(status=status_filter)
        review_reports = review_report_query.order_by(ReviewReport.created_at.desc()).all()
    elif status_filter == "":
        review_reports = review_report_query.order_by(
            case((ReviewReport.status == "pending", 0), else_=1),
            ReviewReport.created_at.desc(),
        ).all()
    else:
        review_reports = []

    query = Report.query
    if status_filter in REPORT_STATUS_LABELS:
        query = query.filter_by(status=status_filter)
        report_list = query.order_by(Report.created_at.desc()).all()
    elif status_filter == "":
        report_list = query.order_by(
            case((Report.status == "pending", 0), else_=1),
            Report.created_at.desc(),
        ).all()
    else:
        report_list = []

    reporter_ids = {report.reporter_id for report in report_list}
    job_ids = {report.target_id for report in report_list if report.target_type == "job"}
    company_ids = {report.target_id for report in report_list if report.target_type == "company"}
    user_ids = {report.target_id for report in report_list if report.target_type == "user"}

    reporters = {u.user_id: u for u in User.query.filter(User.user_id.in_(reporter_ids)).all()}
    job_targets = {j.job_id: j for j in Job.query.filter(Job.job_id.in_(job_ids)).all()}
    company_targets = {c.company_id: c for c in Company.query.filter(Company.company_id.in_(company_ids)).all()}
    user_targets = {u.user_id: u for u in User.query.filter(User.user_id.in_(user_ids)).all()}

    return render_template(
        "admin/reports.html",
        reports=report_list,
        review_reports=review_reports,
        status_filter=status_filter,
        status_labels={**REVIEW_REPORT_STATUS_LABELS, **REPORT_STATUS_LABELS},
        reason_labels=REPORT_REASON_LABELS,
        review_reason_labels=REVIEW_REPORT_REASON_LABELS,
        reporters=reporters,
        job_targets=job_targets,
        company_targets=company_targets,
        user_targets=user_targets,
    )


def review_report_or_404(report_id):
    report = db.session.get(ReviewReport, report_id)
    if report is None:
        abort(404)
    return report


@admin_bp.post("/reports/<int:report_id>/dismiss")
@admin_required
def dismiss_review_report(admin, report_id):
    report = review_report_or_404(report_id)
    if report.status != "pending":
        flash("이미 처리된 신고입니다.", "info")
        return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))

    report.status = "dismissed"
    report.handled_at = datetime.utcnow()
    report.handled_by = admin.user_id
    log_action(admin, "review_report_dismiss", "review", report.review_id)
    _commit("리뷰 신고를 기각했습니다.", "신고를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))


@admin_bp.post("/reports/<int:report_id>/hide")
@admin_required
def hide_reported_review(admin, report_id):
    report = review_report_or_404(report_id)
    if report.status != "pending":
        flash("이미 처리된 신고입니다.", "info")
        return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))

    review = db.session.get(Review, report.review_id)
    if review is None:
        abort(404)

    handled_at = datetime.utcnow()
    review.is_hidden = True
    pending_reports = ReviewReport.query.filter_by(review_id=review.review_id, status="pending").all()
    for pending_report in pending_reports:
        pending_report.status = "hidden"
        pending_report.handled_at = handled_at
        pending_report.handled_by = admin.user_id

    log_action(admin, "review_hide", "review", review.review_id)
    _commit(
        "리뷰를 숨기고 연결된 미처리 신고를 모두 처리했습니다.",
        "신고를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    )
    return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))


@admin_bp.post("/reports/<int:report_id>/resolve")
@admin_required
def resolve_report(admin, report_id):
    report = db.session.get(Report, report_id)
    if report is None:
        abort(404)

    decision = request.form.get("decision")
    if decision not in ("reviewed", "rejected"):
        flash("처리 방법을 선택해 주세요.", "error")
        return redirect(url_for("admin.reports"))

    success_message = "신고를 처리했습니다."
    if decision == "reviewed" and report.target_type == "job":
        reported_job = db.session.get(Job, report.target_id)
        if reported_job is not None:
            reported_job.status = "blocked"
            log_action(admin, "block", "job", reported_job.job_id)
            success_message = "신고를 확인 처리하고 공고를 목록에서 숨겼습니다."

    report.status = decision
    log_action(admin, f"report_{decision}", report.target_type, report.target_id)
    _commit(success_message, "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))


@admin_bp.route("/logs")
@admin_required
def logs(admin):
    log_list = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).limit(200).all()
    admin_ids = {log.admin_id for log in log_list}
    admins = {u.user_id: u for u in User.query.filter(User.user_id.in_(admin_ids)).all()}
    return render_template("admin/logs.html", logs=log_list, admins=admins)
