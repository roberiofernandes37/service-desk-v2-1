from datetime import datetime, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Notification, Ticket, User
from app.services.notifications import notify_many


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_notify_many_deduplicates_and_excludes_current_user(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        db.session.add(profile)
        db.session.flush()
        user = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        other = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=profile.id)
        category = Category(name="GERAL")
        db.session.add_all([user, other, category])
        db.session.flush()
        ticket = Ticket(
            title="Teste",
            description="Escopo",
            priority="Media",
            category_id=category.id,
            requester_id=user.id,
            due_at=datetime.now(timezone.utc),
        )
        db.session.add(ticket)
        db.session.flush()

        notify_many([user.id, other.id, other.id], "Titulo", "Mensagem", ticket_id=ticket.id, exclude_user_id=user.id)
        db.session.commit()

        rows = Notification.query.order_by(Notification.id).all()
        assert len(rows) == 1
        assert rows[0].user_id == other.id
        assert rows[0].ticket_id == ticket.id
