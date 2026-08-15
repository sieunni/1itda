import re
from functools import wraps

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, session, url_for
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import REVIEW_REPORT_REASON_LABELS, Company, Review, ReviewReport, User

reviews_bp = Blueprint("reviews", __name__)
MAX_TITLE_LENGTH = 200
MAX_CONTENT_LENGTH = 10000
MAX_REPORT_DESCRIPTION_LENGTH = 1000

DANGEROUS_REVIEW_TAGS = ("script", "iframe", "object", "embed", "style", "img", "svg")


def sanitize_review_content(content):
    for tag in DANGEROUS_REVIEW_TAGS:
        content = re.sub(rf"<\s*{tag}\b[^>]*>", "", content, flags=re.IGNORECASE)
        content = re.sub(rf"<\s*/\s*{tag}\s*>", "", content, flags=re.IGNORECASE)
    return content


def active_companies_query():
    return Company.query.join(Company.owner).filter(
        User.role == "company",
        User.is_active.is_(True),
    )


def active_company_or_404(company_id):
    company = active_companies_query().filter(Company.company_id == company_id).first()
    if company is None:
        abort(404)
    return company


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))
        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))
        if not user.is_active:
            abort(403)
        return view(user, *args, **kwargs)

    return wrapped_view


def jobseeker_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))
        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            flash("로그인이 필요합니다.", "error")
            return redirect(url_for("auth.login"))
        if not user.is_active or user.role != "jobseeker":
            abort(403)
        return view(user, *args, **kwargs)

    return wrapped_view


def is_active_company_review(review):
    owner = review.company.owner if review.company else None
    return bool(owner and owner.role == "company" and owner.is_active)


def review_or_404(review_id, allow_hidden=False, require_active_company=False):
    review = (
        Review.query.options(
            joinedload(Review.author),
            joinedload(Review.company).joinedload(Company.owner),
        )
        .filter(Review.review_id == review_id)
        .first()
    )
    if (
        review is None
        or (review.is_hidden and not allow_hidden)
        or (require_active_company and not is_active_company_review(review))
    ):
        abort(404)
    return review


def owned_review_or_403(user, review_id):
    review = review_or_404(review_id)
    if review.user_id != user.user_id:
        abort(403)
    return review


def validate_review_form(form):
    company_id_text = (form.get("company_id") or "").strip()
    rating_text = (form.get("rating") or "").strip()
    title = (form.get("title") or "").strip()
    content = (form.get("content") or "").strip()
    errors = []

    try:
        company_id = int(company_id_text)
    except (TypeError, ValueError):
        company_id = None
    company = (
        active_companies_query().filter(Company.company_id == company_id).first()
        if company_id is not None
        else None
    )
    if company is None:
        errors.append("기업을 선택해 주세요.")

    try:
        rating = int(rating_text)
    except (TypeError, ValueError):
        rating = None
    if rating is None:
        errors.append("별점을 선택해 주세요.")
    if not title:
        errors.append("리뷰 제목을 입력해 주세요.")
    elif len(title) > MAX_TITLE_LENGTH:
        errors.append(f"리뷰 제목은 {MAX_TITLE_LENGTH}자 이하여야 합니다.")
    if not content:
        errors.append("리뷰 내용을 입력해 주세요.")
    elif len(content) > MAX_CONTENT_LENGTH:
        errors.append(f"리뷰 내용은 {MAX_CONTENT_LENGTH:,}자 이하여야 합니다.")

    return {
        "company": company,
        "rating": rating,
        "title": title,
        "content": sanitize_review_content(content),
    }, errors


@reviews_bp.get("/reviews")
def review_list():
    companies = active_companies_query().order_by(Company.company_name.asc()).all()
    company_id_text = (request.args.get("company_id") or "").strip()
    selected_company = None
    query = (
        Review.query.options(joinedload(Review.author), joinedload(Review.company))
        .join(Review.company)
        .join(Company.owner)
        .filter(
            Review.is_hidden.is_(False),
            User.role == "company",
            User.is_active.is_(True),
        )
    )
    if company_id_text:
        try:
            company_id = int(company_id_text)
        except ValueError:
            abort(404)
        selected_company = active_company_or_404(company_id)
        query = query.filter(Review.company_id == company_id)
    reviews = query.order_by(Review.created_at.desc(), Review.review_id.desc()).all()
    return render_template("reviews/list.html", reviews=reviews, companies=companies, selected_company=selected_company)


@reviews_bp.get("/companies/<int:company_id>/reviews")
def company_reviews(company_id):
    company = active_company_or_404(company_id)
    reviews = (Review.query.options(joinedload(Review.author)).filter(
        Review.company_id == company_id, Review.is_hidden.is_(False))
               .order_by(Review.created_at.desc(), Review.review_id.desc()).all())
    average_rating = db.session.query(db.func.avg(Review.rating)).filter(
        Review.company_id == company_id, Review.is_hidden.is_(False)).scalar()
    return render_template("reviews/company_list.html", company=company, reviews=reviews,
                           average_rating=float(average_rating) if average_rating is not None else None)


@reviews_bp.get("/reviews/<int:review_id>")
def review_detail(review_id):
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    allow_hidden = bool(user and user.is_active and user.role == "admin")
    response = make_response(
        render_template(
            "reviews/detail.html",
            review=review_or_404(
                review_id,
                allow_hidden=allow_hidden,
                require_active_company=not allow_hidden,
            ),
        )
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
    )
    return response


@reviews_bp.route("/reviews/<int:review_id>/report", methods=["GET", "POST"])
@login_required
def review_report(user, review_id):
    review = review_or_404(review_id, require_active_company=True)
    if user.role == "admin":
        abort(403)
    if review.user_id == user.user_id:
        abort(403)
    existing = ReviewReport.query.filter_by(
        reporter_id=user.user_id,
        review_id=review.review_id,
    ).first()
    if existing is not None:
        flash("이미 신고한 리뷰입니다.", "info")
        return redirect(url_for("reviews.review_detail", review_id=review.review_id))

    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        description = (request.form.get("description") or "").strip()
        errors = []
        if reason not in REVIEW_REPORT_REASON_LABELS:
            errors.append("신고 사유를 선택해 주세요.")
        if len(description) > MAX_REPORT_DESCRIPTION_LENGTH:
            errors.append(f"상세 내용은 {MAX_REPORT_DESCRIPTION_LENGTH:,}자 이하여야 합니다.")
        if not errors:
            db.session.add(
                ReviewReport(
                    review_id=review.review_id,
                    reporter_id=user.user_id,
                    reason=reason,
                    description=description or None,
                )
            )
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("이미 신고한 리뷰입니다.", "info")
            else:
                flash("리뷰 신고가 접수되었습니다.", "success")
            return redirect(url_for("reviews.review_detail", review_id=review.review_id))
        for error in errors:
            flash(error, "error")

    return render_template(
        "reviews/report_form.html",
        review=review,
        reason_labels=REVIEW_REPORT_REASON_LABELS,
    )


@reviews_bp.route("/reviews/new", methods=["GET", "POST"])
@jobseeker_required
def review_new(user):
    companies = active_companies_query().order_by(Company.company_name.asc()).all()
    if request.method == "POST":
        values, errors = validate_review_form(request.form)
        if not errors:
            review = Review(user_id=user.user_id, company_id=values["company"].company_id,
                            rating=values["rating"], title=values["title"], content=values["content"])
            db.session.add(review)
            db.session.commit()
            flash("기업 리뷰가 등록되었습니다.", "success")
            return redirect(url_for("reviews.review_detail", review_id=review.review_id))
        for error in errors:
            flash(error, "error")
    return render_template("reviews/form.html", review=None, companies=companies)


@reviews_bp.route("/reviews/<int:review_id>/edit", methods=["GET", "POST"])
@jobseeker_required
def review_edit(user, review_id):
    review = owned_review_or_403(user, review_id)
    companies = active_companies_query().order_by(Company.company_name.asc()).all()
    if request.method == "POST":
        values, errors = validate_review_form(request.form)
        if not errors:
            review.company_id = values["company"].company_id
            review.rating = values["rating"]
            review.title = values["title"]
            review.content = values["content"]
            db.session.commit()
            flash("기업 리뷰가 수정되었습니다.", "success")
            return redirect(url_for("reviews.review_detail", review_id=review.review_id))
        for error in errors:
            flash(error, "error")
    return render_template("reviews/form.html", review=review, companies=companies)


@reviews_bp.post("/reviews/<int:review_id>/delete")
@jobseeker_required
def review_delete(user, review_id):
    review = owned_review_or_403(user, review_id)
    db.session.delete(review)
    db.session.commit()
    flash("기업 리뷰가 삭제되었습니다.", "success")
    return redirect(url_for("reviews.review_list"))
