from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import AdminActionLog, Category, Company, Job, Report, User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

JOB_STATUS_LABELS = {"pending": "승인 대기", "approved": "공개 중", "blocked": "차단", "closed": "마감"}
REPORT_STATUS_LABELS = {"pending": "미처리", "reviewed": "확인 완료", "rejected": "반려"}
CATEGORY_TYPES = {"region": "지역", "industry": "업종"}


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
        "unverified_companies": Company.query.filter_by(is_verified=False).count(),
        "pending_reports": Report.query.filter_by(status="pending").count(),
    }
    return render_template("admin/dashboard.html", stats=stats)


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


@admin_bp.route("/companies")
@admin_required
def companies(admin):
    company_list = (
        Company.query.join(User, Company.user_id == User.user_id)
        .order_by(Company.created_at.desc())
        .all()
    )
    return render_template("admin/companies.html", companies=company_list)


@admin_bp.post("/companies/<int:company_id>/verify")
@admin_required
def verify_company(admin, company_id):
    company = db.session.get(Company, company_id)
    if company is None:
        abort(404)
    company.is_verified = True
    log_action(admin, "verify", "company", company.company_id)
    _commit("기업 인증을 승인했습니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.companies"))


@admin_bp.post("/companies/<int:company_id>/reject")
@admin_required
def reject_company(admin, company_id):
    company = db.session.get(Company, company_id)
    if company is None:
        abort(404)
    company.is_verified = False
    log_action(admin, "reject", "company", company.company_id)
    _commit("기업 인증을 반려했습니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.companies"))


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
    query = Report.query
    if status_filter in REPORT_STATUS_LABELS:
        query = query.filter_by(status=status_filter)
    report_list = query.order_by(Report.created_at.desc()).all()

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
        status_filter=status_filter,
        status_labels=REPORT_STATUS_LABELS,
        reporters=reporters,
        job_targets=job_targets,
        company_targets=company_targets,
        user_targets=user_targets,
    )


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

    report.status = decision
    log_action(admin, f"report_{decision}", report.target_type, report.target_id)
    _commit("신고를 처리했습니다.", "처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.reports", status=request.args.get("status", "pending")))


@admin_bp.route("/categories")
@admin_required
def categories(admin):
    category_list = Category.query.order_by(Category.type.asc(), Category.name.asc()).all()
    grouped = {"region": [], "industry": []}
    for category in category_list:
        grouped.setdefault(category.type, []).append(category)
    return render_template("admin/categories.html", grouped=grouped, type_labels=CATEGORY_TYPES)


@admin_bp.post("/categories")
@admin_required
def add_category(admin):
    category_type = request.form.get("type", "")
    name = (request.form.get("name") or "").strip()

    if category_type not in CATEGORY_TYPES:
        flash("분류 유형을 선택해 주세요.", "error")
    elif not name:
        flash("분류명을 입력해 주세요.", "error")
    elif Category.query.filter_by(type=category_type, name=name).first():
        flash("이미 등록된 분류입니다.", "error")
    else:
        db.session.add(Category(type=category_type, name=name))
        log_action(admin, "add_category", "category", None)
        _commit("분류를 추가했습니다.", "분류를 추가하지 못했습니다. 잠시 후 다시 시도해 주세요.")

    return redirect(url_for("admin.categories"))


@admin_bp.post("/categories/<int:category_id>/delete")
@admin_required
def delete_category(admin, category_id):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)

    db.session.delete(category)
    log_action(admin, "delete_category", "category", category_id)
    _commit("분류를 삭제했습니다.", "분류를 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return redirect(url_for("admin.categories"))


@admin_bp.route("/logs")
@admin_required
def logs(admin):
    log_list = AdminActionLog.query.order_by(AdminActionLog.created_at.desc()).limit(200).all()
    admin_ids = {log.admin_id for log in log_list}
    admins = {u.user_id: u for u in User.query.filter(User.user_id.in_(admin_ids)).all()}
    return render_template("admin/logs.html", logs=log_list, admins=admins)
