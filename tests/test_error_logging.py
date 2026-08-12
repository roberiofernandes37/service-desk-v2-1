import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import SystemErrorLog
from app.services.error_logging import ERROR_LOG_FILENAME, log_exception


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestingConfig)
    app.config["LOG_ROOT"] = tmp_path
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_error_logging_masks_sensitive_payload(app):
    with app.test_request_context(
        "/solicitacoes/nova?token=abc",
        method="POST",
        data={"titulo": "Cadastro", "senha": "123456", "csrf_token": "raw-token"},
        headers={"User-Agent": "Teste"},
    ):
        log_exception(ValueError("Falha simulada"), status_code=500)

    with app.app_context():
        log = SystemErrorLog.query.one()

        assert log.error_type == "ValueError"
        assert log.error_message == "Falha simulada"
        assert log.path == "/solicitacoes/nova"
        assert log.method == "POST"
        assert log.status_code == 500
        assert log.request_payload["form"]["titulo"] == "Cadastro"
        assert log.request_payload["form"]["senha"] == "***"
        assert log.request_payload["form"]["csrf_token"] == "***"
        assert log.request_payload["args"]["token"] == "***"
        assert (app.config["LOG_ROOT"] / ERROR_LOG_FILENAME).exists()
