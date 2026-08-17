import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, AuditLog, User
from app.routes.admin import apply_audit_filters, build_audit_csv, format_audit_payload


def test_audit_payload_is_formatted_for_display():
    payload = {"status": "Aberta", "id": 10}

    formatted = format_audit_payload(payload)

    assert '"id": 10' in formatted
    assert '"status": "Aberta"' in formatted
    assert formatted.index('"id"') < formatted.index('"status"')


def test_empty_audit_payload_uses_dash():
    assert format_audit_payload(None) == "-"


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_audit_filters_can_be_combined(app):
    with app.app_context():
        profile = AccessProfile(name="Administrador", can_view_reports=True)
        db.session.add(profile)
        db.session.flush()
        user = User(name="Admin", email="admin@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(user)
        db.session.flush()

        matching = AuditLog(user_id=user.id, entity="Ticket", entity_id=5, action="transferred")
        ignored = AuditLog(user_id=user.id, entity="User", entity_id=5, action="updated")
        db.session.add_all([matching, ignored])
        db.session.commit()

        filters = {"entity": "Ticket", "action": "transferred", "user_id": user.id, "entity_id": 5}
        rows = apply_audit_filters(AuditLog.query, filters).all()

        assert rows == [matching]


def test_audit_csv_contains_metadata_and_payload(app):
    with app.app_context():
        profile = AccessProfile(name="Administrador", can_view_reports=True)
        db.session.add(profile)
        db.session.flush()
        user = User(name="Admin", email="admin@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(user)
        db.session.flush()

        log = AuditLog(
            user_id=user.id,
            entity="Ticket",
            entity_id=7,
            action="updated",
            before={"status": "Aberta"},
            after={"status": "Em Andamento"},
            ip_address="127.0.0.1",
        )
        db.session.add(log)
        db.session.commit()

        csv_text = build_audit_csv([log])

        assert "ID;Data;Usuário;Entidade;ID da entidade;Ação;IP;Antes;Depois" in csv_text
        assert "Admin;Ticket;7;updated;127.0.0.1" in csv_text
        assert '""status"": ""Aberta""' in csv_text
        assert '""status"": ""Em Andamento""' in csv_text
