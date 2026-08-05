from flask import Blueprint, render_template

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@jobs_bp.route("/")
def job_list():
    return render_template("placeholder.html", title="공고 목록")


@jobs_bp.route("/<int:job_id>")
def job_detail(job_id):
    return render_template("placeholder.html", title=f"공고 상세 #{job_id}")
