from datetime import timedelta

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User, utc_now


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_kanban_uses_contextual_cards_and_only_recent_completed_tickets(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        user = User(name="Ana Souza", email="ana@example.com", password_hash="hash", profile=profile)
        category = Category(name="GERAL")
        now = utc_now()
        db.session.add_all([profile, user, category])
        db.session.flush()
        for title, status, completed_at in [
            ("Demanda aberta", "Aberta", None),
            ("Demanda pausada", "Pausada", None),
            ("Demanda concluida recente", "Concluida", now - timedelta(days=2)),
            ("Demanda concluida antiga", "Concluida", now - timedelta(days=10)),
        ]:
            db.session.add(
                Ticket(
                    title=title,
                    description="Descricao",
                    priority="Media",
                    status=status,
                    category_id=category.id,
                    requester_id=user.id,
                    due_at=now + timedelta(days=1),
                    completed_at=completed_at,
                    updated_at=completed_at or now,
                )
            )
        db.session.commit()
        user_id = user.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    response = client.get("/solicitacoes/kanban")

    assert response.status_code == 200
    assert b"Abertas" in response.data
    assert b"Em Andamento" in response.data
    assert b"Pausadas" in response.data
    assert b"Conclu\xc3\xaddas" in response.data
    assert b"Canceladas" in response.data
    assert b"Demanda concluida recente" in response.data
    assert b"Demanda concluida antiga" not in response.data
    assert b"SLA Congelado" in response.data
