from functools import wraps
from datetime import date, datetime

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import case
from sqlalchemy.orm import joinedload

from extensions import db
from models import (
    REPORT_REASON_LABELS,
    REVIEW_REPORT_REASON_LABELS,
    AdminActionLog,
    Job,
    Report,
    Review,
    ReviewReport,
    User,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

JOB_STATUS_LABELS = {"pending": "승인 대기", "approved": "공개 중", "blocked": "차단", "closed": "마감"}
REPORT_STATUS_LABELS = {"pending": "미처리", "blocked": "차단 완료", "rejected": "반려", "dismissed": "기각"}
REPORT_FILTER_LABELS = {"": "전체", **REPORT_STATUS_LABELS}
REVIEW_REPORT_STATUS_LABELS = {
    "pending": "미처리",
    "hidden": "리뷰 숨김",
    "dismissed": "기각",
}
REVIEW_REPORT_FILTER_LABELS = {"": "전체", **REVIEW_REPORT_STATUS_LABELS}
REPORT_ALLOWED_TRANSITIONS = {
    "pending": {"block", "reject", "dismiss"},
    "rejected": {"block", "dismiss"},
}


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("session_mode") == "restricted":
            return redirect(url_for("admin.restricted_overview"))

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


@admin_bp.get("/overview")
def restricted_overview():
    if session.get("session_mode") != "restricted":
        abort(404)
    return render_template("admin/overview.html")


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


def _report_status_class(status):
    if status == "blocked":
        return "status-blocked"
    if status == "rejected":
        return "status-rejected"
    if status == "pending":
        return "status-pending"
    return f"status-{status}"


@admin_bp.route("/")
@admin_required
def dashboard(admin):
    stats = {
        "jobseeker_count": User.query.filter_by(role="jobseeker").count(),
        "company_count": User.query.filter_by(role="company").count(),
        "pending_jobs": Job.query.filter_by(status="pending").count(),
        "approved_jobs": Job.query.filter_by(status="approved").count(),
        "blocked_jobs": Job.query.filter_by(status="blocked").count(),
        "hidden_reviews": Review.query.filter_by(is_hidden=True).count(),
        "pending_reports": (
            Report.query.filter_by(status="pending").count()
            + ReviewReport.query.filter_by(status="pending").count()
        ),
    }
    recent_jobs = Job.query.order_by(Job.created_at.desc()).limit(5).all()
    recent_job_reports = (
        Report.query.filter_by(target_type="job")
        .order_by(Report.created_at.desc(), Report.report_id.desc())
        .limit(5)
        .all()
    )
    recent_review_reports = (
        ReviewReport.query.options(
            joinedload(ReviewReport.review).joinedload(Review.company),
            joinedload(ReviewReport.review).joinedload(Review.author),
        )
        .order_by(ReviewReport.created_at.desc(), ReviewReport.report_id.desc())
        .limit(5)
        .all()
    )
    report_job_ids = {
        report.target_id for report in recent_job_reports
    }
    report_jobs = {
        job.job_id: job
        for job in Job.query.filter(Job.job_id.in_(report_job_ids)).all()
    } if report_job_ids else {}
    recent_reports = []
    for report in recent_job_reports:
        target = report_jobs.get(report.target_id)
        recent_reports.append(
            {
                "href": url_for("admin.job_reports", status=report.status),
                "title": target.title if target else "삭제된 공고",
                "kind_label": "공고 신고",
                "reason": REPORT_REASON_LABELS.get(
                    report.reason_category, report.reason_category or "사유 미입력"
                ),
                "created_at": report.created_at,
                "status_label": REPORT_STATUS_LABELS.get(report.status, report.status),
                "status_class": _report_status_class(report.status),
            }
        )
    for report in recent_review_reports:
        review = report.review
        company_name = review.company.company_name if review and review.company else "기업 미정"
        recent_reports.append(
            {
                "href": url_for("admin.review_reports", status=report.status),
                "title": review.title if review else "삭제된 리뷰",
                "kind_label": "리뷰 신고",
                "reason": REVIEW_REPORT_REASON_LABELS.get(report.reason, report.reason),
                "created_at": report.created_at,
                "status_label": REVIEW_REPORT_STATUS_LABELS.get(report.status, report.status),
                "status_class": f"status-{report.status}",
                "context": company_name,
            }
        )
    recent_reports = sorted(
        recent_reports,
        key=lambda item: item["created_at"] or datetime.min,
        reverse=True,
    )[:5]
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_jobs=recent_jobs,
        recent_reports=recent_reports,
        job_status_labels=JOB_STATUS_LABELS,
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
    if job.status != "pending":
        flash("승인 대기 공고만 승인할 수 있습니다.", "error")
        return redirect(url_for("admin.jobs", status=request.args.get("status", "")))
    if job.deadline and job.deadline < date.today():
        flash("마감일이 지난 공고는 승인할 수 없습니다.", "error")
        return redirect(url_for("admin.jobs", status=request.args.get("status", "")))
    job.reviewed_title = job.title
    job.reviewed_content = job.content
    job.reviewed_company_description = job.company.description if job.company else None
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
    if job.status not in {"pending", "approved"}:
        flash("승인 대기 또는 공개 중인 공고만 차단할 수 있습니다.", "error")
        return redirect(url_for("admin.jobs", status=request.args.get("status", "")))
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
    if job.status != "blocked":
        flash("차단된 공고만 차단 해제할 수 있습니다.", "error")
        return redirect(url_for("admin.jobs", status=request.args.get("status", "")))
    job.status = "pending"
    log_action(admin, "unblock", "job", job.job_id)
    _commit("차단을 해제했습니다. 재승인이 필요합니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.jobs", status=request.args.get("status", "")))


@admin_bp.route("/reports")
@admin_required
def reports(admin):
    job_report_query = Report.query.filter_by(target_type="job")
    job_report_counts = {
        "total": job_report_query.count(),
        **{
            status: job_report_query.filter_by(status=status).count()
            for status in REPORT_STATUS_LABELS
        },
    }
    review_report_query = ReviewReport.query
    review_report_counts = {
        "total": review_report_query.count(),
        **{
            status: review_report_query.filter_by(status=status).count()
            for status in REVIEW_REPORT_STATUS_LABELS
        },
    }
    return render_template(
        "admin/reports.html",
        job_report_counts=job_report_counts,
        review_report_counts=review_report_counts,
    )


@admin_bp.route("/reports/jobs")
@admin_required
def job_reports(admin):
    status_filter = request.args.get("status", "pending")
    allowed_filters = set(REPORT_FILTER_LABELS)
    if status_filter not in allowed_filters:
        status_filter = "pending"

    query = Report.query.filter_by(target_type="job")
    if status_filter in REPORT_STATUS_LABELS:
        query = query.filter_by(status=status_filter)
    if status_filter == "":
        query = query.order_by(
            case((Report.status == "pending", 0), else_=1),
            Report.created_at.desc(),
            Report.report_id.desc(),
        )
    else:
        query = query.order_by(Report.created_at.desc(), Report.report_id.desc())
    report_list = query.all()

    reporter_ids = {report.reporter_id for report in report_list}
    job_ids = {report.target_id for report in report_list}

    reporters = (
        {u.user_id: u for u in User.query.filter(User.user_id.in_(reporter_ids)).all()}
        if reporter_ids
        else {}
    )
    job_targets = (
        {j.job_id: j for j in Job.query.filter(Job.job_id.in_(job_ids)).all()}
        if job_ids
        else {}
    )

    return render_template(
        "admin/job_reports.html",
        reports=report_list,
        status_filter=status_filter,
        status_labels=REPORT_FILTER_LABELS,
        report_status_labels=REPORT_STATUS_LABELS,
        reason_labels=REPORT_REASON_LABELS,
        reporters=reporters,
        job_targets=job_targets,
    )


@admin_bp.get("/reports/jobs/<int:report_id>/preview")
@admin_required
def preview_job_report(admin, report_id):
    report = db.session.get(Report, report_id)
    if report is None or report.target_type != "job":
        abort(404)
    reason = report.reason or ""
    reason = reason.replace("<script>", "").replace("</script>", "")
    response = make_response(
        render_template(
            "admin/job_report_preview.html",
            report=report,
            reason=reason,
            reason_label=REPORT_REASON_LABELS.get(
                report.reason_category,
                report.reason_category or "미입력",
            ),
        )
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
    )
    return response


@admin_bp.route("/reports/reviews")
@admin_required
def review_reports(admin):
    status_filter = request.args.get("status", "pending")
    allowed_filters = set(REVIEW_REPORT_FILTER_LABELS)
    if status_filter not in allowed_filters:
        status_filter = "pending"

    query = ReviewReport.query.options(
        joinedload(ReviewReport.review).joinedload(Review.company),
        joinedload(ReviewReport.review).joinedload(Review.author),
        joinedload(ReviewReport.reporter),
    )
    if status_filter in REVIEW_REPORT_STATUS_LABELS:
        query = query.filter_by(status=status_filter)
    if status_filter == "":
        query = query.order_by(
            case((ReviewReport.status == "pending", 0), else_=1),
            ReviewReport.created_at.desc(),
            ReviewReport.report_id.desc(),
        )
    else:
        query = query.order_by(ReviewReport.created_at.desc(), ReviewReport.report_id.desc())

    return render_template(
        "admin/review_reports.html",
        review_reports=query.all(),
        status_filter=status_filter,
        status_labels=REVIEW_REPORT_FILTER_LABELS,
        review_report_status_labels=REVIEW_REPORT_STATUS_LABELS,
        review_reason_labels=REVIEW_REPORT_REASON_LABELS,
    )


def review_report_or_404(report_id):
    report = db.session.get(ReviewReport, report_id)
    if report is None:
        abort(404)
    return report


@admin_bp.post("/reports/<int:report_id>/dismiss")
@admin_required
def dismiss_review_report(admin, report_id):
    return _dismiss_review_report(admin, report_id)


@admin_bp.post("/reports/reviews/<int:report_id>/dismiss")
@admin_required
def dismiss_review_report_from_review_reports(admin, report_id):
    return _dismiss_review_report(admin, report_id)


def _dismiss_review_report(admin, report_id):
    report = review_report_or_404(report_id)
    if report.status != "pending":
        flash("이미 처리된 신고입니다.", "info")
        return redirect(url_for("admin.review_reports", status=request.args.get("status", "pending")))

    report.status = "dismissed"
    report.handled_at = datetime.utcnow()
    report.handled_by = admin.user_id
    log_action(admin, "review_report_dismiss", "review", report.review_id)
    _commit("리뷰 신고를 기각했습니다.", "신고를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.review_reports", status=request.args.get("status", "pending")))


@admin_bp.post("/reports/<int:report_id>/hide")
@admin_required
def hide_reported_review(admin, report_id):
    return _hide_reported_review(admin, report_id)


@admin_bp.post("/reports/reviews/<int:report_id>/hide")
@admin_required
def hide_reported_review_from_review_reports(admin, report_id):
    return _hide_reported_review(admin, report_id)


def _hide_reported_review(admin, report_id):
    report = review_report_or_404(report_id)
    if report.status != "pending":
        flash("이미 처리된 신고입니다.", "info")
        return redirect(url_for("admin.review_reports", status=request.args.get("status", "pending")))

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
    return redirect(url_for("admin.review_reports", status=request.args.get("status", "pending")))


@admin_bp.post("/reports/<int:report_id>/resolve")
@admin_required
def resolve_report(admin, report_id):
    return _resolve_job_report(admin, report_id)


@admin_bp.post("/reports/jobs/<int:report_id>/resolve")
def resolve_job_report(report_id):
    user_id = session.get("user_id")
    if not user_id:
        flash("로그인이 필요합니다.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if user is None:
        session.pop("user_id", None)
        flash("로그인이 필요합니다.", "error")
        return redirect(url_for("auth.login"))
    if not user.is_active:
        abort(403)

    return _resolve_job_report(user, report_id)


def _resolve_job_report(admin, report_id):
    report = db.session.get(Report, report_id)
    if report is None or report.target_type != "job":
        abort(404)

    decision = request.form.get("decision")
    allowed_decisions = REPORT_ALLOWED_TRANSITIONS.get(report.status, set())
    if decision not in allowed_decisions:
        flash("처리 방법을 선택해 주세요.", "error")
        return redirect(url_for("admin.job_reports", status=request.args.get("status", "pending")))

    success_message = "신고를 처리했습니다."
    report_status = {
        "block": "blocked",
        "reject": "rejected",
        "dismiss": "dismissed",
    }[decision]

    if decision == "block":
        reported_job = db.session.get(Job, report.target_id)
        if reported_job is not None:
            if reported_job.status == "closed":
                success_message = "마감된 공고 신고를 차단 완료로 처리했습니다. 공고 상태는 마감으로 유지했습니다."
            elif reported_job.status == "blocked":
                success_message = "이미 차단된 공고 신고를 차단 완료로 처리했습니다."
            else:
                reported_job.status = "blocked"
                log_action(admin, "block", "job", reported_job.job_id)
                success_message = "신고를 확인 처리하고 공고를 차단했습니다."
            related_reports = Report.query.filter_by(
                target_type="job",
                target_id=report.target_id,
                status="pending",
            ).all()
            for related_report in related_reports:
                related_report.status = "blocked"
        else:
            success_message = "신고를 차단 완료로 처리했습니다. 대상 공고는 확인할 수 없습니다."

    report.status = report_status
    if decision == "reject":
        success_message = "신고를 반려했습니다."
    elif decision == "dismiss":
        success_message = "신고를 기각했습니다."

    log_action(admin, f"report_{decision}", report.target_type, report.target_id)
    _commit(success_message, "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.job_reports", status=request.args.get("status", "pending")))


@admin_bp.route("/logs")
@admin_required
def logs(admin):
    log_list = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).limit(200).all()
    admin_ids = {log.admin_id for log in log_list}
    admins = {u.user_id: u for u in User.query.filter(User.user_id.in_(admin_ids)).all()}
    return render_template("admin/logs.html", logs=log_list, admins=admins)
