from flask import Blueprint, render_template

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/join")
def join():
    return render_template("auth/join.html")


@auth_bp.route("/login")
def login():
    return render_template("auth/login.html")


@auth_bp.route("/logout")
def logout():
    return render_template("placeholder.html", title="로그아웃")
