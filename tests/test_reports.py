from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.routes.admin import build_report_metrics


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def add_ticket(title, category, requester, status, priority, due_at):
    ticket = Ticket(
        title=title,
        description="Teste",
        category_id=category.id,
        requester_id=requester.id,
        status=status,
        priority=priority,
        due_at=due_at,
    )
    db.session.add(ticket)
    return ticket


def test_report_metrics_count_sla_and_groupings(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(requester)
        db.session.flush()

        now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        add_ticket("Atrasada", category, requester, "Aberta", "Urgente", now - timedelta(days=1))
        add_ticket("Hoje", category, requester, "Em Andamento", "Media", now.replace(hour=23))
        add_ticket("Concluida", category, requester, "Concluida", "Baixa", now - timedelta(days=2))
        db.session.commit()

        metrics = build_report_metrics(now)

        assert metrics["total"] == 3
        assert metrics["active"] == 2
        assert metrics["concluded"] == 1
        assert metrics["overdue"] == 1
        assert metrics["due_today"] == 1
        assert metrics["by_status"]["Aberta"] == 1
        assert metrics["by_priority"]["Urgente"] == 1
        assert metrics["by_category"] == [("GERAL", 3)]
