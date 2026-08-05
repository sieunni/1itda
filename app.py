from flask import Flask, render_template

from config import Config
from extensions import csrf, db
from models import Job
from routes.admin import admin_bp
from routes.applications import applications_bp
from routes.auth import auth_bp
from routes.company import company_bp
from routes.jobs import jobs_bp
from routes.profile import profile_bp


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

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
