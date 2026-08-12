import click
import hmac
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import inspect, text
from urllib.parse import urlsplit

from config import Config
from extensions import csrf, db
from job_lifecycle import close_expired_jobs
from models import Job, User
from routes.admin import admin_bp
from routes.applications import applications_bp
from routes.auth import auth_bp
from routes.chat import chat_bp
from routes.company import company_bp
from routes.jobs import jobs_bp
from routes.profile import profile_bp
from routes.reviews import reviews_bp
from session_security import auth_fingerprint


def ensure_schema_compatibility():
    """Apply small, data-preserving schema updates for existing SQLite databases."""
    inspector = inspect(db.engine)
    report_columns = {column["name"] for column in inspector.get_columns("reports")}
    if "reason_category" not in report_columns:
        db.session.execute(text("ALTER TABLE reports ADD COLUMN reason_category VARCHAR(30)"))
        db.session.commit()

    review_columns = {column["name"] for column in inspector.get_columns("reviews")}
    if "is_hidden" not in review_columns:
        db.session.execute(
            text("ALTER TABLE reviews ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()

    job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    if "updated_at" not in job_columns:
        db.session.execute(text("ALTER TABLE jobs ADD COLUMN updated_at DATETIME"))
        db.session.execute(
            text(
                "UPDATE jobs "
                "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
            )
        )
        db.session.commit()

    resume_columns = {column["name"] for column in inspector.get_columns("resumes")}
    if "is_deleted" not in resume_columns:
        db.session.execute(
            text("ALTER TABLE resumes ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()

    if "view_count" not in job_columns:
        db.session.execute(text("ALTER TABLE jobs ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0"))
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    secret_key = app.config.get("SECRET_KEY")
    if not secret_key or len(secret_key) < 32:
        raise RuntimeError(
            "SECRET_KEY must be set to an environment-specific value of at least 32 characters."
        )

    base_url = urlsplit(app.config["APP_BASE_URL"])
    if base_url.scheme not in {"http", "https"} or not base_url.netloc:
        raise RuntimeError("APP_BASE_URL must be an absolute http:// or https:// URL.")

    db.init_app(app)
    csrf.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(applications_bp)
    app.register_blueprint(company_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reviews_bp)

    @app.before_request
    def reject_inactive_session():
        if request.endpoint == "static" or request.endpoint == "auth.logout":
            return None

        user_id = session.get("user_id")
        if not user_id:
            return None

        user = db.session.get(User, user_id)
        fingerprint = session.get("auth_fingerprint")
        if (
            user is not None
            and user.is_active
            and fingerprint
            and hmac.compare_digest(fingerprint, auth_fingerprint(user))
        ):
            return None

        session.clear()
        if user is None or (user is not None and user.is_active):
            message = "사용자 정보를 확인할 수 없습니다."
        else:
            message = "관리자에 의해 차단된 계정입니다."

        if request.endpoint in {"chat.send_message", "chat.poll_messages"}:
            return jsonify({"error": message}), 403

        flash(message, "error")
        if request.endpoint == "auth.login":
            return None
        return redirect(url_for("auth.login"))

    @app.before_request
    def synchronize_expired_jobs():
        close_expired_jobs()

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; "
            "form-action 'self'",
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.cli.command("close-expired-jobs")
    def close_expired_jobs_command():
        """Close jobs whose deadline has passed; safe to call from cron."""
        count = close_expired_jobs()
        click.echo(f"Closed {count} expired job(s).")

    @app.context_processor
    def inject_current_user():
        user_id = session.get("user_id")
        return {"current_user": User.query.get(user_id) if user_id else None}

    @app.route("/")
    def index():
        latest_jobs = (
            Job.query.filter_by(status="approved")
            .order_by(Job.created_at.desc())
            .limit(6)
            .all()
        )
        return render_template("index.html", jobs=latest_jobs)

    with app.app_context():
        db.create_all()
        ensure_schema_compatibility()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
