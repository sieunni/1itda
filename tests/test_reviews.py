import os
import shutil
import tempfile
import unittest


TEST_DIR = tempfile.mkdtemp(prefix="1itda-review-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TEST_DIR, "test.db").replace("\\", "/")
os.environ["SECRET_KEY"] = "review-test-secret"

from app import app
from extensions import db
from models import Company, Review, User


class ReviewFeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    def setUp(self):
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()
            owner = User(email="company@example.com", password_hash="unused", role="company", name="기업 담당자")
            self.author = User(email="author@example.com", password_hash="unused", role="jobseeker", name="작성자")
            self.other = User(email="other@example.com", password_hash="unused", role="jobseeker", name="다른 사용자")
            db.session.add_all([owner, self.author, self.other])
            db.session.flush()
            self.company = Company(user_id=owner.user_id, company_name="테스트 기업")
            db.session.add(self.company)
            db.session.commit()
            self.ids = {"owner": owner.user_id, "author": self.author.user_id,
                        "other": self.other.user_id, "company": self.company.company_id}

    def login_as(self, user_id, role):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role

    def create_review(self):
        with app.app_context():
            review = Review(user_id=self.ids["author"], company_id=self.ids["company"],
                            rating=4, title="좋은 개발 문화", content="팀 분위기가 좋았습니다.")
            db.session.add(review)
            db.session.commit()
            return review.review_id

    def test_anonymous_review_list(self):
        response = self.client.get("/reviews")
        self.assertEqual(response.status_code, 200)
        self.assertIn("기업 리뷰".encode(), response.data)

    def test_jobseeker_create_detail_edit_delete(self):
        self.login_as(self.ids["author"], "jobseeker")
        response = self.client.post("/reviews/new", data={"company_id": self.ids["company"],
                                    "rating": "5", "title": "첫 리뷰", "content": "좋은 경험"})
        self.assertEqual(response.status_code, 302)
        detail_url = response.headers["Location"]
        self.assertIn("/reviews/", detail_url)
        self.assertIn("첫 리뷰".encode(), self.client.get(detail_url).data)
        review_id = int(detail_url.rstrip("/").split("/")[-1])

        response = self.client.post(f"/reviews/{review_id}/edit",
                                    data={"company_id": self.ids["company"], "rating": "3",
                                          "title": "수정된 리뷰", "content": "수정된 내용"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("수정된 리뷰".encode(), self.client.get(detail_url).data)
        self.assertEqual(self.client.post(f"/reviews/{review_id}/delete").status_code, 302)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

    def test_other_user_cannot_edit_or_delete(self):
        review_id = self.create_review()
        self.login_as(self.ids["other"], "jobseeker")
        self.assertEqual(self.client.get(f"/reviews/{review_id}/edit").status_code, 403)
        self.assertEqual(self.client.post(f"/reviews/{review_id}/delete").status_code, 403)
        with app.app_context():
            self.assertIsNotNone(db.session.get(Review, review_id))

    def test_company_account_cannot_create(self):
        self.login_as(self.ids["owner"], "company")
        self.assertEqual(self.client.get("/reviews/new").status_code, 403)
        self.assertEqual(self.client.post("/reviews/new", data={"company_id": self.ids["company"],
                         "rating": "5", "title": "금지", "content": "금지"}).status_code, 403)

    def test_not_found_review_and_company(self):
        self.assertEqual(self.client.get("/reviews/999999").status_code, 404)
        self.assertEqual(self.client.get("/companies/999999/reviews").status_code, 404)
        self.assertEqual(self.client.get("/reviews?company_id=999999").status_code, 404)

    def test_company_page_uses_real_average(self):
        review_id = self.create_review()
        with app.app_context():
            db.session.add(Review(user_id=self.ids["other"], company_id=self.ids["company"],
                                  rating=2, title="두 번째", content="내용"))
            db.session.commit()
        response = self.client.get(f"/companies/{self.ids['company']}/reviews")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"3.0", response.data)
        self.assertIn(str(review_id).encode(), response.data)

    def test_validation_rejects_invalid_input(self):
        self.login_as(self.ids["author"], "jobseeker")
        response = self.client.post("/reviews/new", data={"company_id": "999999", "rating": "6",
                                    "title": "", "content": ""})
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            self.assertEqual(Review.query.count(), 0)

    def test_delete_is_post_only_and_csrf_protected(self):
        review_id = self.create_review()
        self.login_as(self.ids["author"], "jobseeker")
        self.assertEqual(self.client.get(f"/reviews/{review_id}/delete").status_code, 405)
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            self.assertEqual(self.client.post(f"/reviews/{review_id}/delete").status_code, 400)
        finally:
            app.config["WTF_CSRF_ENABLED"] = False

    def test_existing_pages_smoke(self):
        for path in ("/", "/login", "/join", "/jobs", "/company/dashboard"):
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, (200, 302))
        self.login_as(self.ids["owner"], "company")
        self.assertEqual(self.client.get("/company/jobs").status_code, 200)


if __name__ == "__main__":
    unittest.main()
