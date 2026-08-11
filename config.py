import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# DB/업로드 파일이 저장될 디렉터리를 앱 시작 시점에 보장한다.
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "uploads"), exist_ok=True)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', '1itda.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 업로드 최대 5MB

    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    PASSWORD_RESET_MAX_AGE = int(os.environ.get("PASSWORD_RESET_MAX_AGE", "1800"))
    MAIL_HOST = os.environ.get("MAIL_HOST")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_FROM = os.environ.get("MAIL_FROM") or MAIL_USERNAME or "noreply@1itda.local"
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "1") == "1"
