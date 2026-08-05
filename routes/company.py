from flask import Blueprint, render_template

company_bp = Blueprint("company", __name__, url_prefix="/company")


@company_bp.route("/jobs")
def dashboard():
    return render_template("placeholder.html", title="기업 대시보드")


@company_bp.route("/jobs/new")
def job_new():
    return render_template("placeholder.html", title="공고 등록")


@company_bp.route("/applicants")
def applicants():
    return render_template("placeholder.html", title="지원자 조회")
