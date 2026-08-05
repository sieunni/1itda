from flask import Blueprint, render_template

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/mypage")
def mypage():
    return render_template("placeholder.html", title="마이페이지")
