from flask import Blueprint, render_template

applications_bp = Blueprint("applications", __name__)


@applications_bp.route("/jobs/<int:job_id>/apply")
def apply(job_id):
    return render_template("placeholder.html", title="입사지원")


@applications_bp.route("/mypage/applications")
def my_applications():
    return render_template("placeholder.html", title="지원 내역")
