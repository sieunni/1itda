from datetime import date, datetime
from math import ceil

from flask import Blueprint, abort, render_template, request
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from models import Company, Job

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")

PAGE_SIZE = 12
MAX_KEYWORD_LENGTH = 60


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
        "deadline_label": deadline["label"],
        "is_closed": deadline["is_closed"],
    }


def _approved_jobs_query():
    return Job.query.options(joinedload(Job.company)).filter(Job.status == "approved")


def _filter_options(column):
    rows = (
        Job.query.with_entities(column)
        .filter(Job.status == "approved", column.isnot(None), column != "")
        .distinct()
        .order_by(column.asc())
        .all()
    )
    return [value for (value,) in rows]


@jobs_bp.route("", methods=["GET"])
@jobs_bp.route("/", methods=["GET"])
def job_list():
    keyword = _clean_text(request.args.get("keyword"), MAX_KEYWORD_LENGTH)
    region = _clean_text(request.args.get("region"), 80)
    industry = _clean_text(request.args.get("industry"), 80)
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
        query.order_by(Job.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    return render_template(
        "jobs/list.html",
        jobs=[_job_to_view(job) for job in jobs],
        filters={"keyword": keyword, "region": region, "industry": industry},
        regions=_filter_options(Job.region),
        industries=_filter_options(Job.industry),
        total=total,
        page=page,
        total_pages=total_pages,
        has_filters=bool(keyword or region or industry),
    )


@jobs_bp.route("/<int:job_id>", methods=["GET"])
def job_detail(job_id):
    job = _approved_jobs_query().filter(Job.job_id == job_id).first()

    if job is None:
        abort(404)

    return render_template("jobs/detail.html", job=_job_to_view(job))
