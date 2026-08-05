from flask import Blueprint, render_template

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
def dashboard():
    return render_template("placeholder.html", title="관리자 대시보드")
