from datetime import datetime

from extensions import db


class User(db.Model):
    __tablename__ = "users"

    user_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # jobseeker / company / admin
    name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    company = db.relationship("Company", back_populates="owner", uselist=False)
    reviews = db.relationship("Review", back_populates="author")
    review_reports = db.relationship(
        "ReviewReport",
        back_populates="reporter",
        foreign_keys="ReviewReport.reporter_id",
    )
    handled_review_reports = db.relationship(
        "ReviewReport",
        back_populates="handler",
        foreign_keys="ReviewReport.handled_by",
    )


class Company(db.Model):
    __tablename__ = "companies"

    company_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    company_name = db.Column(db.String(120), nullable=False)
    is_verified = db.Column(db.Boolean, default=False, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    owner = db.relationship("User", back_populates="company")
    jobs = db.relationship("Job", back_populates="company")
    reviews = db.relationship("Review", back_populates="company")


class Review(db.Model):
    __tablename__ = "reviews"
    __table_args__ = (
        db.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),
    )

    review_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.company_id"), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_hidden = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    author = db.relationship("User", back_populates="reviews")
    company = db.relationship("Company", back_populates="reviews")
    review_reports = db.relationship("ReviewReport", back_populates="review")


REVIEW_REPORT_REASON_LABELS = {
    "false_info": "허위 정보",
    "abuse": "욕설/비방",
    "advertising": "광고",
    "privacy": "개인정보 노출",
    "etc": "기타",
}


class ReviewReport(db.Model):
    __tablename__ = "review_reports"
    __table_args__ = (
        db.UniqueConstraint("reporter_id", "review_id", name="uq_review_reporter_review"),
    )

    report_id = db.Column(db.Integer, primary_key=True)
    review_id = db.Column(db.Integer, db.ForeignKey("reviews.review_id"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    reason = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    handled_at = db.Column(db.DateTime)
    handled_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))

    review = db.relationship("Review", back_populates="review_reports")
    reporter = db.relationship("User", back_populates="review_reports", foreign_keys=[reporter_id])
    handler = db.relationship("User", back_populates="handled_review_reports", foreign_keys=[handled_by])


class Job(db.Model):
    __tablename__ = "jobs"

    job_id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.company_id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    region = db.Column(db.String(80))
    industry = db.Column(db.String(80))
    deadline = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending/approved/blocked/closed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    company = db.relationship("Company", back_populates="jobs")
    applications = db.relationship("Application", back_populates="job")
    scraps = db.relationship("Scrap", back_populates="job")


class Resume(db.Model):
    __tablename__ = "resumes"

    resume_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    file_path = db.Column(db.String(255))  # 서버 저장 경로(난수 파일명)
    original_filename = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Application(db.Model):
    __tablename__ = "applications"
    __table_args__ = (db.UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),)

    application_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.job_id"), nullable=False)
    resume_id = db.Column(db.Integer, db.ForeignKey("resumes.resume_id"))
    resume_snapshot = db.Column(db.Text)  # 지원 당시 이력서 스냅샷(보존용)
    status = db.Column(db.String(20), nullable=False, default="submitted")  # submitted/cancelled/accepted/rejected
    applied_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    job = db.relationship("Job", back_populates="applications")
    status_history = db.relationship("ApplicationStatusHistory", back_populates="application")
    chat_messages = db.relationship("ChatMessage", back_populates="application")


class ChatMessage(db.Model):
    __tablename__ = "chat_messages"

    message_id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.application_id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    is_system = db.Column(db.Boolean, default=False, nullable=False)  # 합격/불합격 자동 통보 메시지 여부
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    application = db.relationship("Application", back_populates="chat_messages")


class ApplicationStatusHistory(db.Model):
    __tablename__ = "application_status_history"

    history_id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("applications.application_id"), nullable=False)
    old_status = db.Column(db.String(20))
    new_status = db.Column(db.String(20))
    changed_by = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    application = db.relationship("Application", back_populates="status_history")


class Scrap(db.Model):
    __tablename__ = "scraps"
    __table_args__ = (db.UniqueConstraint("user_id", "job_id", name="uq_scrap_user_job"),)

    scrap_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("jobs.job_id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    job = db.relationship("Job", back_populates="scraps")


REPORT_REASON_LABELS = {
    "false_info": "허위 채용정보",
    "spam": "중복/스팸성 공고",
    "excessive_info": "개인정보 과다 요구",
    "inappropriate": "부적절하거나 차별적인 내용",
    "fraud_suspected": "채용 사기 의심",
    "etc": "기타",
}


class Report(db.Model):
    __tablename__ = "reports"

    report_id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    target_type = db.Column(db.String(20), nullable=False)  # job / company / user
    target_id = db.Column(db.Integer, nullable=False)
    reason_category = db.Column(db.String(30))  # REPORT_REASON_LABELS 키
    reason = db.Column(db.Text)  # "기타" 상세 사유 또는 추가 설명
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending/reviewed/rejected/dismissed
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AdminActionLog(db.Model):
    __tablename__ = "admin_action_logs"

    log_id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    action_type = db.Column(db.String(40))  # 승인/차단/삭제 등
    target_type = db.Column(db.String(20))  # job/company/user/report
    target_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
