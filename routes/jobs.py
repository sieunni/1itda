from datetime import date, datetime
from math import ceil

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import case, or_, update
from sqlalchemy.orm import joinedload

from choices import INDUSTRY_CHOICES, REGION_CHOICES
from extensions import db
from models import Application, REPORT_REASON_LABELS, Company, Job, Report, Scrap, User

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")

PAGE_SIZE = 12
MAX_KEYWORD_LENGTH = 60
SORT_OPTIONS = (
    ("latest", "최신순"),
    ("updated", "수정순"),
    ("deadline", "마감순"),
    ("views", "조회수순"),
)
SORT_VALUES = {value for value, _label in SORT_OPTIONS}


def _clean_text(value, max_length):
    return (value or "").strip()[:max_length]


def _parse_page(value):
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1

    return max(page, 1)


def _to_date(value):
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if not value:
        return None

    text = str(value).strip()
    for date_format in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue

    return None


def _format_date(value, fallback="미정"):
    parsed = _to_date(value)
    if parsed:
        return parsed.strftime("%Y-%m-%d")

    return str(value).strip() if value else fallback


def _deadline_info(value):
    deadline = _to_date(value)

    if not value:
        return {
            "display": "상시 채용",
            "label": "상시 채용",
            "is_closed": False,
        }

    if not deadline:
        return {
            "display": str(value),
            "label": "마감일 확인",
            "is_closed": False,
        }

    days_left = (deadline - date.today()).days
    if days_left < 0:
        label = "마감"
        is_closed = True
    elif days_left == 0:
        label = "오늘 마감"
        is_closed = False
    else:
        label = f"D-{days_left}"
        is_closed = False

    return {
        "display": deadline.strftime("%Y-%m-%d"),
        "label": label,
        "is_closed": is_closed,
    }


def _job_to_view(job):
    deadline = _deadline_info(job.deadline)
    company = job.company
    is_closed = job.status == "closed" or deadline["is_closed"]

    return {
        "job_id": job.job_id,
        "title": job.title,
        "content": job.content,
        "company_name": company.company_name if company else "기업명 미정",
        "company_description": company.description if company else "",
        "region": job.region or "지역 미정",
        "industry": job.industry or "업종 미정",
        "created_at": _format_date(job.created_at),
        "deadline": deadline["display"],
        "deadline_label": "마감" if is_closed else deadline["label"],
        "is_closed": is_closed,
        "view_count": job.view_count or 0,
    }


def _approved_jobs_query():
    return Job.query.options(joinedload(Job.company)).filter(Job.status == "approved")


def _job_order_by(sort):
    if sort == "updated":
        return (Job.updated_at.desc(), Job.created_at.desc(), Job.job_id.desc())
    if sort == "deadline":
        today = date.today()
        deadline_group = case(
            (Job.deadline < today, 2),
            (Job.deadline.is_(None), 1),
            else_=0,
        )
        active_deadline = case((Job.deadline >= today, Job.deadline), else_=None)
        expired_deadline = case((Job.deadline < today, Job.deadline), else_=None)
        return (
            deadline_group.asc(),
            active_deadline.asc(),
            expired_deadline.desc(),
            Job.created_at.desc(),
            Job.job_id.desc(),
        )
    if sort == "views":
        return (Job.view_count.desc(), Job.created_at.desc(), Job.job_id.desc())

    return (Job.created_at.desc(), Job.job_id.desc())


@jobs_bp.route("", methods=["GET"])
@jobs_bp.route("/", methods=["GET"])
def job_list():
    keyword = _clean_text(request.args.get("keyword"), MAX_KEYWORD_LENGTH)
    region = _clean_text(request.args.get("region"), 80)
    if region not in REGION_CHOICES:
        region = ""
    industry = _clean_text(request.args.get("industry"), 80)
    if industry not in INDUSTRY_CHOICES:
        industry = ""
    sort = _clean_text(request.args.get("sort", "latest"), 20)
    if sort not in SORT_VALUES:
        sort = "latest"
    page = _parse_page(request.args.get("page"))

    query = _approved_jobs_query().outerjoin(Job.company)

    if keyword:
        keyword_pattern = f"%{keyword}%"
        query = query.filter(
            or_(
                Job.title.ilike(keyword_pattern),
                Job.content.ilike(keyword_pattern),
                Company.company_name.ilike(keyword_pattern),
            )
        )

    if region:
        query = query.filter(Job.region == region)

    if industry:
        query = query.filter(Job.industry == industry)

    total = query.count()
    total_pages = max(ceil(total / PAGE_SIZE), 1)
    if total and page > total_pages:
        page = total_pages

    jobs = (
        query.order_by(*_job_order_by(sort))
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    return render_template(
        "jobs/list.html",
        jobs=[_job_to_view(job) for job in jobs],
        filters={"keyword": keyword, "region": region, "industry": industry, "sort": sort},
        regions=REGION_CHOICES,
        industries=INDUSTRY_CHOICES,
        sort_options=SORT_OPTIONS,
        total=total,
        page=page,
        total_pages=total_pages,
        has_filters=bool(keyword or region or industry),
    )


@jobs_bp.route("/<int:job_id>", methods=["GET"])
def job_detail(job_id):
    job = Job.query.options(joinedload(Job.company)).filter(Job.job_id == job_id).first()

    if job is None:
        abort(404)

    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    is_public = job.status == "approved"
    is_owner = bool(
        user
        and user.role == "company"
        and job.company
        and job.company.user_id == user.user_id
    )
    is_admin = bool(user and user.role == "admin")
    is_applicant = bool(
        user_id
        and job.status == "closed"
        and Application.query.filter_by(user_id=user_id, job_id=job.job_id).first()
    )

    # Closed jobs stay hidden from the public, while people who actually
    # applied retain access to the posting attached to their application.
    if not is_public and not (is_owner or is_admin or is_applicant):
        abort(404)
    is_preview = not is_public and not is_applicant

    if job.status == "approved":
        viewed_job_ids = set(session.get("viewed_job_ids", []))
        if job.job_id not in viewed_job_ids:
            # Increment in the database so simultaneous first views do not
            # overwrite one another with the same value.
            db.session.execute(
                update(Job)
                .where(Job.job_id == job.job_id)
                .values(view_count=Job.view_count + 1)
            )
            db.session.commit()
            db.session.refresh(job)
            viewed_job_ids.add(job.job_id)
            session["viewed_job_ids"] = sorted(viewed_job_ids)[-200:]

    is_scrapped = False
    is_reported = False
    if not is_preview and user_id:
        is_scrapped = (
            Scrap.query.filter_by(user_id=user_id, job_id=job.job_id).first() is not None
        )
        is_reported = (
            Report.query.filter_by(
                reporter_id=user_id,
                target_type="job",
                target_id=job.job_id,
            ).first()
            is not None
        )

    return render_template(
        "jobs/detail.html",
        job=_job_to_view(job),
        is_preview=is_preview,
        job_status=job.status,
        is_scrapped=is_scrapped,
        is_reported=is_reported,
        report_reason_labels=REPORT_REASON_LABELS,
    )


@jobs_bp.post("/<int:job_id>/scrap")
def scrap_job(job_id):
    job = _approved_jobs_query().filter(Job.job_id == job_id).first()
    if job is None:
        abort(404)

    user_id = session.get("user_id")
    if not user_id:
        flash("로그인 후 스크랩할 수 있습니다.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        session.clear()
        flash("로그인 후 스크랩할 수 있습니다.", "error")
        return redirect(url_for("auth.login"))

    if user.role != "jobseeker":
        abort(403)

    existing_scrap = Scrap.query.filter_by(user_id=user.user_id, job_id=job.job_id).first()
    if existing_scrap is None:
        db.session.add(Scrap(user_id=user.user_id, job_id=job.job_id))
        db.session.commit()
        flash("공고를 스크랩했습니다. 마이페이지에서 확인할 수 있습니다.", "success")
    else:
        db.session.delete(existing_scrap)
        db.session.commit()
        flash("스크랩을 해제했습니다.", "success")

    return redirect(url_for("jobs.job_detail", job_id=job.job_id))


@jobs_bp.post("/<int:job_id>/report")
def report_job(job_id):
    job = _approved_jobs_query().filter(Job.job_id == job_id).first()
    if job is None:
        abort(404)

    user_id = session.get("user_id")
    if not user_id:
        flash("로그인이 필요한 기능입니다.", "error")
        return redirect(url_for("auth.login"))

    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        session.clear()
        flash("로그인이 필요한 기능입니다.", "error")
        return redirect(url_for("auth.login"))

    if user.role != "jobseeker":
        abort(403)

    existing_report = Report.query.filter_by(
        reporter_id=user.user_id,
        target_type="job",
        target_id=job.job_id,
    ).first()
    if existing_report is not None:
        flash("이미 신고 이력이 있는 공고입니다. 같은 공고는 다시 신고할 수 없습니다.", "info")
        return redirect(url_for("jobs.job_detail", job_id=job.job_id))

    reason_category = request.form.get("reason_category", "")
    reason_detail = (request.form.get("reason_detail") or "").strip()[:500]

    if reason_category not in REPORT_REASON_LABELS:
        flash("신고 사유를 선택해 주세요.", "error")
        return redirect(url_for("jobs.job_detail", job_id=job.job_id))
    if reason_category == "etc" and not reason_detail:
        flash("기타 사유를 입력해 주세요.", "error")
        return redirect(url_for("jobs.job_detail", job_id=job.job_id))

    db.session.add(
        Report(
            reporter_id=user.user_id,
            target_type="job",
            target_id=job.job_id,
            reason_category=reason_category,
            reason=reason_detail or None,
        )
    )
    db.session.commit()
    flash("공고 신고가 접수되었습니다. 관리자가 확인할 예정입니다.", "success")
    return redirect(url_for("jobs.job_detail", job_id=job.job_id))
