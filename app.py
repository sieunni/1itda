import click
import hmac
import secrets
import threading
import time
from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import inspect, text

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
from session_security import auth_fingerprint, is_session_fresh


def relax_review_rating_constraint():
    if db.engine.dialect.name != "sqlite":
        return

    table_sql = db.session.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reviews'")
    ).scalar()
    if not table_sql or "ck_review_rating" not in table_sql:
        return

    db.session.commit()
    connection = db.engine.raw_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute(
            """
            CREATE TABLE reviews_relaxed (
                review_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                title VARCHAR(200) NOT NULL,
                content TEXT NOT NULL,
                is_hidden BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (review_id),
                FOREIGN KEY(user_id) REFERENCES users (user_id),
                FOREIGN KEY(company_id) REFERENCES companies (company_id)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO reviews_relaxed (
                review_id, user_id, company_id, rating, title, content,
                is_hidden, created_at, updated_at
            )
            SELECT
                review_id, user_id, company_id, rating, title, content,
                is_hidden, created_at, updated_at
            FROM reviews
            """
        )
        cursor.execute("DROP TABLE reviews")
        cursor.execute("ALTER TABLE reviews_relaxed RENAME TO reviews")
        connection.commit()
    finally:
        cursor.execute("PRAGMA foreign_keys=ON")
        connection.commit()
        cursor.close()
        connection.close()


def ensure_schema_compatibility():
    inspector = inspect(db.engine)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "profile_image_url" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN profile_image_url VARCHAR(1000)"))
        db.session.commit()

    report_columns = {column["name"] for column in inspector.get_columns("reports")}
    if "reason_category" not in report_columns:
        db.session.execute(text("ALTER TABLE reports ADD COLUMN reason_category VARCHAR(30)"))
        db.session.commit()
    db.session.execute(
        text(
            "UPDATE reports "
            "SET status = 'blocked' "
            "WHERE target_type = 'job' AND status = 'reviewed'"
        )
    )
    db.session.commit()

    review_columns = {column["name"] for column in inspector.get_columns("reviews")}
    if "is_hidden" not in review_columns:
        db.session.execute(
            text("ALTER TABLE reviews ADD COLUMN is_hidden BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    relax_review_rating_constraint()

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

    if "reviewed_title" not in job_columns:
        db.session.execute(text("ALTER TABLE jobs ADD COLUMN reviewed_title VARCHAR(200)"))
        db.session.commit()

    if "reviewed_content" not in job_columns:
        db.session.execute(text("ALTER TABLE jobs ADD COLUMN reviewed_content TEXT"))
        db.session.commit()

    if "reviewed_company_description" not in job_columns:
        db.session.execute(text("ALTER TABLE jobs ADD COLUMN reviewed_company_description TEXT"))
        db.session.commit()

    report_indexes = {index["name"] for index in inspector.get_indexes("reports")}
    if "uq_reporter_target" not in report_indexes:
        duplicate = db.session.execute(
            text(
                "SELECT 1 FROM reports "
                "GROUP BY reporter_id, target_type, target_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate:
            raise RuntimeError(
                "Duplicate report rows must be resolved before applying the security index."
            )
        db.session.execute(
            text(
                "CREATE UNIQUE INDEX uq_reporter_target "
                "ON reports (reporter_id, target_type, target_id)"
            )
        )
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    secret_key = app.config.get("SECRET_KEY")
    if app.config["APP_ENV"] == "production" and (
        not secret_key or len(secret_key) < 32
    ):
        raise RuntimeError(
            "Production SECRET_KEY must be set to an environment-specific value of at least 32 characters."
        )
    if app.config["APP_ENV"] == "production" and app.config["DEBUG"]:
        raise RuntimeError("FLASK_DEBUG must be disabled in production.")
    if app.config["APP_ENV"] == "production" and not app.config["SESSION_COOKIE_SECURE"]:
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")

    db.init_app(app)
    csrf.init_app(app)
    lifecycle_lock = threading.Lock()
    last_lifecycle_sync = 0.0

    app.register_blueprint(auth_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(applications_bp)
    app.register_blueprint(company_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reviews_bp)

    @app.before_request
    def prepare_content_security_policy_nonce():
        g.csp_nonce = secrets.token_urlsafe(18)

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
            and is_session_fresh(user)
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
        nonlocal last_lifecycle_sync
        if session.get("session_mode") == "restricted":
            return None
        now = time.monotonic()
        if now - last_lifecycle_sync < 60:
            return None
        with lifecycle_lock:
            if now - last_lifecycle_sync < 60:
                return None
            close_expired_jobs()
            last_lifecycle_sync = now
        return None

    @app.after_request
    def add_security_headers(response):
        csp_nonce = getattr(g, "csp_nonce", secrets.token_urlsafe(18))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            f"default-src 'self'; script-src 'self' 'nonce-{csp_nonce}'; "
            "style-src 'self'; img-src 'self' data:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; "
            "form-action 'self'",
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.endpoint and (
            request.endpoint.startswith(("profile.", "applications.", "company.", "admin.", "chat."))
            or session.get("user_id")
        ):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.cli.command("close-expired-jobs")
    def close_expired_jobs_command():
        count = close_expired_jobs()
        click.echo(f"Closed {count} expired job(s).")

    @app.context_processor
    def inject_current_user():
        user_id = session.get("user_id")
        return {
            "current_user": db.session.get(User, user_id) if user_id else None,
            "csp_nonce": g.csp_nonce,
        }

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
