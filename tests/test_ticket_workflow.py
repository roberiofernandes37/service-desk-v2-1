from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.services import ticket_workflow
from app.services.ticket_workflow import DONE, IN_PROGRESS, PAUSED, active_seconds, apply_action


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_ticket():
    profile = AccessProfile(name="Atendente", can_work_tickets=True)
    user = User(name="Bia", email="bia@example.com", password_hash="hash", profile=profile)
    category = Category(name="GERAL")
    start = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    ticket = Ticket(
        title="Teste de fluxo",
        description="Descricao",
        category=category,
        requester=user,
        due_at=start + timedelta(days=1),
        created_at=start,
    )
    db.session.add_all([profile, user, category, ticket])
    db.session.flush()
    return ticket, user, start


def test_ticket_workflow_records_assignment_pause_resume_and_completion(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(ticket_workflow, "audit", lambda *args, **kwargs: None)
        ticket, user, start = make_ticket()

        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: start)
        apply_action(ticket, user, "assumir")
        assert ticket.status == IN_PROGRESS
        assert ticket.assignee_id == user.id

        paused_at = start + timedelta(hours=2)
        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: paused_at)
        apply_action(ticket, user, "pausar", note="Aguardando retorno do usuario")
        assert ticket.status == PAUSED
        assert ticket.pause_started_at == paused_at

        resumed_at = paused_at + timedelta(hours=1)
        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: resumed_at)
        apply_action(ticket, user, "retomar")
        assert ticket.status == IN_PROGRESS
        assert ticket.total_paused_seconds == 3600
        assert ticket.pause_started_at is None
        assert ticket.due_at == start + timedelta(days=1, hours=1)

        completed_at = resumed_at + timedelta(hours=2)
        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: completed_at)
        apply_action(ticket, user, "concluir", note="Solucao aplicada")
        db.session.commit()

        assert ticket.status == DONE
        assert ticket.completed_at.replace(tzinfo=timezone.utc) == completed_at
        assert ticket.resolved_by_id == user.id
        assert ticket.resolution_note == "Solucao aplicada"
        assert active_seconds(ticket, completed_at) == 4 * 3600
        assert [item.action for item in ticket.history] == ["assumir", "pausar", "retomar", "concluir"]


def test_reopening_clears_completion_metadata(app, monkeypatch):
    with app.app_context():
        monkeypatch.setattr(ticket_workflow, "audit", lambda *args, **kwargs: None)
        ticket, user, start = make_ticket()
        completed_at = start + timedelta(hours=2)
        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: completed_at)
        apply_action(ticket, user, "assumir")
        apply_action(ticket, user, "concluir", note="Resolvida")
        assert ticket.completed_at == completed_at

        monkeypatch.setattr(ticket_workflow, "now_utc", lambda: completed_at + timedelta(minutes=5))
        apply_action(ticket, user, "reabrir")

        assert ticket.status == IN_PROGRESS
        assert ticket.completed_at is None
        assert ticket.resolved_by_id is None


def test_ticket_detail_renders_operational_actions_by_status(app):
    with app.app_context():
        ticket, user, _ = make_ticket()
        ticket.status = IN_PROGRESS
        ticket.assignee_id = user.id
        db.session.commit()
        ticket_id = ticket.id
        user_id = user.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    response = client.get(f"/solicitacoes/{ticket_id}")

    assert response.status_code == 200
    assert b"data-action=\"pausar\"" in response.data
    assert b"data-action=\"concluir\"" in response.data
    assert b"data-action=\"assumir\"" not in response.data
    assert b"ticketActionModal" in response.data
