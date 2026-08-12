import click
from flask import Flask, render_template, session
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
    def synchronize_expired_jobs():
        close_expired_jobs()

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
