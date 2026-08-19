from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import sys

from werkzeug.security import generate_password_hash

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import app
from extensions import db
from models import Company, Job, User

JOBSEEKER_EMAIL = "test@test.com"
COMPANY_EMAIL = "co@test.com"
ADMIN_EMAIL = "admin@test.com"
TEST_ACCOUNT_PASSWORD = "test1234"

COMPANY_NAME = "1ITDA 상세테스트 기업"
COMPANY_DESCRIPTION = "공고 상세 페이지의 기업 소개 영역을 확인하기 위한 테스트 기업입니다."

JOB_ROWS = [
    {
        "title": "[상세테스트] Flask 백엔드 개발자",
        "content": (
            "Flask, SQLAlchemy 기반 채용 플랫폼 백엔드 기능을 개발합니다.\n"
            "공고 상세 페이지에서 줄바꿈, 긴 본문, 기업 소개 노출을 확인하기 위한 테스트 데이터입니다."
        ),
        "region": "서울",
        "industry": "웹 서비스",
        "deadline": date.today() + timedelta(days=14),
        "status": "approved",
    },
    {
        "title": "[상세테스트] 데이터 엔지니어",
        "content": (
            "데이터 파이프라인과 분석용 적재 구조를 설계합니다.\n"
            "마감 임박 상태와 D-day 표시를 확인하기 위한 공고입니다."
        ),
        "region": "경기",
        "industry": "데이터/AI",
        "deadline": date.today() + timedelta(days=3),
        "status": "approved",
    },
    {
        "title": "[상세테스트] 상시 채용 프론트엔드 개발자",
        "content": (
            "HTML, CSS, JavaScript 기반 사용자 화면을 구현합니다.\n"
            "마감일이 없는 상시 채용 표시를 확인합니다."
        ),
        "region": "부산",
        "industry": "프론트엔드",
        "deadline": None,
        "status": "approved",
    },
    {
        "title": "[상세테스트] 마감 표시 검증용 공고",
        "content": "승인 공고이지만 마감일이 지난 경우 상세 화면의 마감 상태를 확인합니다.",
        "region": "대전",
        "industry": "클라우드",
        "deadline": date.today() - timedelta(days=2),
        "status": "approved",
    },
    {
        "title": "[상세테스트] 비공개 접근 차단 검증용 공고",
        "content": "pending 상태이므로 목록과 상세에서 일반 사용자에게 노출되면 안 됩니다.",
        "region": "서울",
        "industry": "보안",
        "deadline": date.today() + timedelta(days=20),
        "status": "pending",
    },
]


def now_naive_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upsert_user(email, role, name, password):
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            name=name,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
    else:
        user.password_hash = generate_password_hash(password)
        user.role = role
        user.name = name
        user.is_active = True
    return user


def upsert_company(user):
    company = Company.query.filter_by(user_id=user.user_id).first()
    if company is None:
        company = Company(
            user_id=user.user_id,
            company_name=COMPANY_NAME,
            is_verified=True,
            description=COMPANY_DESCRIPTION,
        )
        db.session.add(company)
        db.session.flush()
    else:
        company.company_name = COMPANY_NAME
        company.is_verified = True
        company.description = COMPANY_DESCRIPTION
    return company


def upsert_jobs(company):
    existing_jobs = (
        Job.query.filter_by(company_id=company.company_id)
        .order_by(Job.job_id.asc())
        .all()
    )
    seeded_jobs = []

    for index, values in enumerate(JOB_ROWS):
        if index < len(existing_jobs):
            job = existing_jobs[index]
            for field, value in values.items():
                setattr(job, field, value)
        else:
            job = Job(company_id=company.company_id, created_at=now_naive_utc(), **values)
            db.session.add(job)
            db.session.flush()
        seeded_jobs.append(job)

    return seeded_jobs


def main():
    if os.environ.get("APP_ENV") != "development":
        raise RuntimeError("Test data seeding is allowed only when APP_ENV=development.")
    seed_password = os.environ.get("SEED_USER_PASSWORD", TEST_ACCOUNT_PASSWORD)

    with app.app_context():
        company_user = upsert_user(COMPANY_EMAIL, "company", "상세테스트 기업 담당자", seed_password)
        upsert_user(JOBSEEKER_EMAIL, "jobseeker", "테스트 구직자", seed_password)
        upsert_user(ADMIN_EMAIL, "admin", "관리자", seed_password)
        company = upsert_company(company_user)
        jobs = upsert_jobs(company)
        db.session.commit()

        print("Seeded Korean test data.")
        print(f"jobseeker_email={JOBSEEKER_EMAIL}")
        print(f"company_email={COMPANY_EMAIL}")
        print(f"admin_email={ADMIN_EMAIL}")
        for job in jobs:
            visibility = "public" if job.status == "approved" else "private"
            print(f"{visibility}: /jobs/{job.job_id} - {job.title}")


if __name__ == "__main__":
    main()
