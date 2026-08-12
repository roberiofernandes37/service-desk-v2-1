import os
from pathlib import Path


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///../instance/service_desk_v2_1.sqlite3",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_TIME_LIMIT = 3600
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    UPLOAD_ROOT = os.environ.get("UPLOAD_ROOT", "uploads")
    LOG_ROOT = os.environ.get("LOG_ROOT", "logs")
    BACKUP_ROOT = os.environ.get("BACKUP_ROOT", "backups")
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"

    @staticmethod
    def init_app(app):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        Path(app.config["UPLOAD_ROOT"]).mkdir(parents=True, exist_ok=True)
        Path(app.config["LOG_ROOT"]).mkdir(parents=True, exist_ok=True)
        Path(app.config["BACKUP_ROOT"]).mkdir(parents=True, exist_ok=True)


class ProductionConfig(Config):
    @staticmethod
    def init_app(app):
        Config.init_app(app)
        if app.config["SECRET_KEY"] in {"", "dev-only-change-me"}:
            raise RuntimeError("SECRET_KEY must be set for production")


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


def load_config():
    env = os.environ.get("FLASK_ENV", "development").lower()
    if env == "production":
        return ProductionConfig
    if env == "testing":
        return TestingConfig
    return Config
