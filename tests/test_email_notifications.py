import json
from pathlib import Path

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, User, UserNotificationPreference
from app.services.notifications import send_event_emails


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestingConfig)
    app.config["MAIL_CONFIG_PATH"] = str(tmp_path / "mail_config.json")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def login(client, user_id):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def test_user_can_choose_email_events_without_enabling_creation_or_assignment(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        user = User(name="Ana", email="ana@example.com", password_hash="hash", profile=profile)
        db.session.add_all([profile, user])
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    login(client, user_id)
    response = client.post(
        "/perfil/notificacoes",
        data={
            "email_enabled": "y",
            "concluida": "y",
            "comentario": "y",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        preferences = UserNotificationPreference.query.one()
        assert preferences.email_enabled is True
        assert set(preferences.email_events) == {"concluida", "comentario"}
        assert "criada" not in preferences.email_events
        assert "atribuida" not in preferences.email_events


def test_only_selected_email_events_are_sent(app, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.notifications.send_email_safely",
        lambda recipient, subject, body, **kwargs: sent.append((recipient, subject)),
    )
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        user = User(name="Ana", email="ana@example.com", password_hash="hash", profile=profile)
        preferences = UserNotificationPreference(email_enabled=True, email_events=["concluida"], user=user)
        db.session.add_all([profile, user, preferences])
        db.session.commit()

        send_event_emails([user.id], "pausada", "Pausada", "Mensagem")
        send_event_emails([user.id], "concluida", "Concluida", "Mensagem")

    assert len(sent) == 1
    assert sent[0][0] == "ana@example.com"


def test_admin_can_save_smtp_configuration_without_saving_password_to_database(app):
    with app.app_context():
        profile = AccessProfile(name="Administrador", can_manage_settings=True)
        user = User(name="Admin", email="admin@example.com", password_hash="hash", profile=profile)
        db.session.add_all([profile, user])
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    login(client, user_id)
    response = client.post(
        "/admin/email",
        data={
            "server": "smtp.example.com",
            "port": "587",
            "username": "service@example.com",
            "password": "senha-secreta",
            "sender": "service@example.com",
            "test_recipient": "teste@example.com",
            "use_tls": "y",
            "save": "1",
        },
    )

    assert response.status_code == 302
    config = json.loads(Path(app.config["MAIL_CONFIG_PATH"]).read_text(encoding="utf-8"))
    assert config["server"] == "smtp.example.com"
    assert config["password"] == "senha-secreta"
    assert config["test_recipient"] == "teste@example.com"
