from datetime import datetime, timezone

import pytest
from werkzeug.exceptions import Forbidden, NotFound

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.security import can_view_ticket
from app.services.uploads import send_protected_upload


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_user(name, email, profile):
    user = User(name=name, email=email, password_hash="hash", profile_id=profile.id)
    db.session.add(user)
    db.session.flush()
    return user


def test_requester_can_view_own_ticket_but_unrelated_user_cannot(app):
    with app.app_context():
        requester_profile = AccessProfile(name="Solicitante")
        worker_profile = AccessProfile(name="Atendente", can_work_tickets=True)
        db.session.add_all([requester_profile, worker_profile])
        db.session.flush()

        requester = make_user("Ana", "ana@example.com", requester_profile)
        unrelated = make_user("Bia", "bia@example.com", requester_profile)
        worker = make_user("Caio", "caio@example.com", worker_profile)
        category = Category(name="GERAL")
        db.session.add(category)
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

        assert can_view_ticket(requester, ticket)
        assert can_view_ticket(worker, ticket)
        assert not can_view_ticket(unrelated, ticket)


def test_upload_download_rejects_path_traversal(app, tmp_path):
    app.config["UPLOAD_ROOT"] = str(tmp_path)
    with app.app_context():
        with pytest.raises((Forbidden, NotFound)):
            send_protected_upload("../secret.txt")
