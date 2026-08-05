from flask import Blueprint, render_template

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/join")
def join():
    return render_template("placeholder.html", title="회원가입")


@auth_bp.route("/login")
def login():
    return render_template("placeholder.html", title="로그인")


@auth_bp.route("/logout")
def logout():
    return render_template("placeholder.html", title="로그아웃")
