from datetime import datetime, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.routes.tickets import can_edit_ticket


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_ticket_editing_is_limited_to_open_ticket_owner_or_manager(app):
    with app.app_context():
        requester_profile = AccessProfile(name="Solicitante")
        manager_profile = AccessProfile(name="Gestor", can_manage_settings=True)
        db.session.add_all([requester_profile, manager_profile])
        db.session.flush()

        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=requester_profile.id)
        other = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=requester_profile.id)
        manager = User(name="Caio", email="caio@example.com", password_hash="hash", profile_id=manager_profile.id)
        category = Category(name="GERAL")
        db.session.add_all([requester, other, manager, category])
        db.session.flush()

        ticket = Ticket(
            title="Teste",
            description="Escopo",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=datetime.now(timezone.utc),
        )
        db.session.add(ticket)
        db.session.commit()

        assert can_edit_ticket(requester, ticket)
        assert can_edit_ticket(manager, ticket)
        assert not can_edit_ticket(other, ticket)

        ticket.status = "Em Andamento"
        assert not can_edit_ticket(requester, ticket)
        assert not can_edit_ticket(manager, ticket)
