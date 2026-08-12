import atexit
import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta


TEST_DIR = tempfile.mkdtemp(prefix="1itda-security-tests-")
atexit.register(shutil.rmtree, TEST_DIR, ignore_errors=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TEST_DIR, "test.db").replace("\\", "/")
os.environ["SECRET_KEY"] = "security-test-secret"

from app import app
from extensions import db
from job_lifecycle import close_expired_jobs
from models import Application, Company, Job, LoginThrottle, Resume, User
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
                "admin": self.admin.user_id,
                "expired_approved": jobs[0].job_id,
                "expired_pending": jobs[1].job_id,
                "active": jobs[2].job_id,
            }

    def test_expired_approved_job_closes_but_pending_job_stays_private(self):
        with app.app_context():
            self.assertEqual(close_expired_jobs(), 1)
            self.assertEqual(db.session.get(Job, self.ids["expired_approved"]).status, "closed")
            self.assertEqual(db.session.get(Job, self.ids["expired_pending"]).status, "pending")

        self.assertEqual(self.client.get(f'/jobs/{self.ids["expired_approved"]}').status_code, 404)
        self.assertEqual(self.client.get(f'/jobs/{self.ids["expired_pending"]}').status_code, 404)

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

            with self.client.session_transaction() as session:
                session["user_id"] = self.ids["user"]
                session["role"] = "jobseeker"
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

        with self.client.session_transaction() as session:
            session["user_id"] = self.ids["user"]
            session["role"] = "jobseeker"
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

        with self.client.session_transaction() as session:
            session["user_id"] = self.ids["user"]
            session["role"] = "jobseeker"
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
        with self.client.session_transaction() as session:
            session["user_id"] = self.ids["user"]
            session["role"] = "jobseeker"
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

    def test_logged_in_user_can_change_password_from_mypage(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.ids["user"]
            session["role"] = "jobseeker"

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

    def test_logout_is_post_only_and_csrf_protected(self):
        with self.client.session_transaction() as session:
            session["user_id"] = self.ids["user"]
            session["role"] = "jobseeker"

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

if __name__ == "__main__":
    unittest.main()
