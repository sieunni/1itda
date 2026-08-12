import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# DB/업로드 파일이 저장될 디렉터리를 앱 시작 시점에 보장한다.
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "uploads"), exist_ok=True)


class Config:
    APP_ENV = os.environ.get("APP_ENV", "production").lower()
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', '1itda.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 업로드 최대 5MB
    MAX_FORM_MEMORY_SIZE = 1 * 1024 * 1024
    MAX_FORM_PARTS = 50
    TRUSTED_HOSTS = [
        host.strip()
        for host in os.environ.get("TRUSTED_HOSTS", "localhost,127.0.0.1").split(",")
        if host.strip()
    ]

    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE", "1" if APP_ENV == "production" else "0"
    ) == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_IDLE_SECONDS = int(os.environ.get("SESSION_IDLE_SECONDS", "1800"))
    ADMIN_SESSION_IDLE_SECONDS = int(os.environ.get("ADMIN_SESSION_IDLE_SECONDS", "900"))
    SESSION_ABSOLUTE_SECONDS = int(os.environ.get("SESSION_ABSOLUTE_SECONDS", "28800"))
