from flask import Blueprint, abort, flash, g, jsonify, make_response, redirect, render_template, request, session, url_for
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import Application, ChatMessage, Company, Job, User

chat_bp = Blueprint("chat", __name__)

MAX_MESSAGE_LENGTH = 1000


def _current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def _application_for_chat_or_404(application_id, user):
    application = (
        Application.query.options(joinedload(Application.job))
        .filter_by(application_id=application_id)
        .first()
    )
    if application is None:
        abort(404)

    is_applicant = application.user_id == user.user_id
    is_company_owner = bool(
        user.role == "company"
        and application.job
        and application.job.company
        and application.job.company.user_id == user.user_id
    )
    if not is_applicant and not is_company_owner:
        abort(403)

    return application


def _serialize_message(message, viewer):
    return {
        "message_id": message.message_id,
        "content": message.content,
        "is_system": message.is_system,
        "is_mine": message.sender_id == viewer.user_id,
        "created_at": message.created_at.strftime("%Y-%m-%d %H:%M"),
    }


@chat_bp.route("/chat")
def chat_list():
    user = _current_user()
    if not user:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))

    if user.role == "jobseeker":
        applications = (
            Application.query.options(joinedload(Application.job).joinedload(Job.company))
            .filter_by(user_id=user.user_id)
            .all()
        )
    elif user.role == "company":
        applications = (
            Application.query.options(joinedload(Application.job).joinedload(Job.company))
            .join(Job, Application.job_id == Job.job_id)
            .join(Company, Job.company_id == Company.company_id)
            .filter(Company.user_id == user.user_id)
            .all()
        )
    else:
        abort(403)

    applicants_by_id = {}
    if user.role == "company" and applications:
        applicant_ids = {application.user_id for application in applications}
        applicants_by_id = {u.user_id: u for u in User.query.filter(User.user_id.in_(applicant_ids)).all()}

    latest_by_application = {}
    application_ids = [application.application_id for application in applications]
    if application_ids:
        latest_ids = (
            db.session.query(
                ChatMessage.application_id,
                func.max(ChatMessage.message_id).label("latest_id"),
            )
            .filter(ChatMessage.application_id.in_(application_ids))
            .group_by(ChatMessage.application_id)
            .subquery()
        )
        latest_messages = ChatMessage.query.join(
            latest_ids, ChatMessage.message_id == latest_ids.c.latest_id
        ).all()
        latest_by_application = {message.application_id: message for message in latest_messages}

    conversations = [
        {
            "application": application,
            "latest_message": latest_by_application.get(application.application_id),
            "applicant": applicants_by_id.get(application.user_id),
            "peer": (
                applicants_by_id.get(application.user_id)
                if user.role == "company"
                else (application.job.company.owner if application.job and application.job.company else None)
            ),
        }
        for application in applications
    ]
    conversations.sort(
        key=lambda c: c["latest_message"].created_at if c["latest_message"] else c["application"].applied_at,
        reverse=True,
    )

    response = make_response(render_template("chat/list.html", conversations=conversations))
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src 'self' 'nonce-{g.csp_nonce}'; "
        f"style-src 'self' 'nonce-{g.csp_nonce}'; img-src 'self' data: https:; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'"
    )
    return response


@chat_bp.route("/applications/<int:application_id>/chat")
def chat_thread(application_id):
    user = _current_user()
    if not user:
        flash("로그인 후 이용해 주세요.", "error")
        return redirect(url_for("auth.login"))

    application = _application_for_chat_or_404(application_id, user)
    messages = (
        ChatMessage.query.filter_by(application_id=application.application_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return render_template("chat/thread.html", application=application, messages=messages)


@chat_bp.post("/applications/<int:application_id>/chat/messages")
def send_message(application_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "로그인이 필요합니다."}), 401

    application = _application_for_chat_or_404(application_id, user)

    content = (request.form.get("content") or "").strip()[:MAX_MESSAGE_LENGTH]
    if not content:
        return jsonify({"error": "메시지를 입력해 주세요."}), 400

    message = ChatMessage(
        application_id=application.application_id,
        sender_id=user.user_id,
        content=content,
    )
    db.session.add(message)
    db.session.commit()

    return jsonify(_serialize_message(message, user))


@chat_bp.get("/applications/<int:application_id>/chat/messages")
def poll_messages(application_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "로그인이 필요합니다."}), 401

    _application_for_chat_or_404(application_id, user)

    after_id = request.args.get("after", type=int) or 0
    messages = (
        ChatMessage.query.filter(
            ChatMessage.application_id == application_id,
            ChatMessage.message_id > after_id,
        )
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return jsonify([_serialize_message(message, user) for message in messages])
