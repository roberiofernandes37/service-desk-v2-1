from datetime import datetime, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.routes.tickets import can_transfer_ticket


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_ticket(category, requester, status="Aberta"):
    ticket = Ticket(
        title="Transferencia",
        description="Teste",
        priority="Media",
        status=status,
        category_id=category.id,
        requester_id=requester.id,
        due_at=datetime.now(timezone.utc),
    )
    db.session.add(ticket)
    db.session.flush()
    return ticket


def test_ticket_transfer_is_limited_to_active_tickets_and_workers(app):
    with app.app_context():
        requester_profile = AccessProfile(name="Solicitante")
        worker_profile = AccessProfile(name="Atendente", can_work_tickets=True)
        db.session.add_all([requester_profile, worker_profile])
        db.session.flush()

        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=requester_profile.id)
        worker = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=worker_profile.id)
        category = Category(name="GERAL")
        db.session.add_all([requester, worker, category])
        db.session.flush()

        assert can_transfer_ticket(worker, make_ticket(category, requester, "Aberta"))
        assert can_transfer_ticket(worker, make_ticket(category, requester, "Em Andamento"))
        assert not can_transfer_ticket(worker, make_ticket(category, requester, "Concluida"))
        assert not can_transfer_ticket(worker, make_ticket(category, requester, "Cancelada"))
        assert not can_transfer_ticket(requester, make_ticket(category, requester, "Aberta"))
