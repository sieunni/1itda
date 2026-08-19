# 1ITDA(일잇다)

1ITDA는 구직자와 기업을 연결하는 IT 분야 채용 플랫폼입니다. 구직자는 채용공고 조회와 지원, 이력서 관리, 기업 리뷰 및 채팅 기능을 이용할 수 있습니다. 기업은 채용공고와 지원자를 관리하며, 관리자는 공고 승인과 신고 처리를 담당합니다.

## 주요 기능

- 구직자·기업·관리자 역할별 회원 및 세션 관리
- 채용공고 등록, 승인, 조회, 수정 및 지원
- 이력서 업로드, 관리 및 미리보기
- 기업 리뷰와 신고 처리
- 지원자와 기업 간 1:1 채팅
- 관리자 대시보드와 사용자 관리

## 기술 스택

- Python 3.12
- Flask 3.1
- Flask-SQLAlchemy
- Flask-WTF
- SQLite
- Jinja2, HTML, CSS, JavaScript

## 설치 방법

저장소를 내려받은 뒤 프로젝트 디렉터리에서 다음 명령을 실행합니다.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell에서는 가상환경을 다음 명령으로 활성화합니다.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

## 테스트 데이터 생성

개발용 테스트 계정과 채용공고를 생성합니다. 같은 명령을 다시 실행하면 기존 테스트 계정과 공고가 갱신됩니다.

```bash
python scripts/seed_test_data.py
```

| 역할 | 이메일 | 비밀번호 |
| --- | --- | --- |
| 구직자 | `test@test.com` | `test1234` |
| 기업 | `co@test.com` | `test1234` |
| 관리자 | `admin@test.com` | `test1234` |

비밀번호를 변경하려면 `.env`의 `SEED_USER_PASSWORD` 값을 수정한 뒤 시드 스크립트를 다시 실행합니다.

## 실행 방법

```bash
python app.py
```

브라우저에서 `http://127.0.0.1:5000`으로 접속합니다. 데이터베이스는 최초 실행 시 `instance/1itda.db`에 생성됩니다.

## 테스트 실행

```bash
python -m unittest discover -s tests
```

## 환경설정

주요 환경변수는 `.env.example`에서 확인할 수 있습니다. 실제 운영 환경에서는 별도의 강력한 `SECRET_KEY`를 설정하고 HTTPS 환경에 맞게 `SESSION_COOKIE_SECURE=1`을 적용해야 합니다.
