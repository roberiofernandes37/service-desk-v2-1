from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, User
from app.routes.main import dashboard_charts, dashboard_counts, dashboard_response


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def add_ticket(title, category, requester, status, due_at, assignee_id=None):
    ticket = Ticket(
        title=title,
        description="Teste",
        priority="Media",
        status=status,
        category_id=category.id,
        requester_id=requester.id,
        assignee_id=assignee_id,
        due_at=due_at,
    )
    db.session.add(ticket)
    return ticket


def test_dashboard_counts_include_due_today_and_unassigned(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(requester)
        db.session.flush()

        now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        add_ticket("Hoje", category, requester, "Aberta", now.replace(hour=23))
        add_ticket("Atrasada", category, requester, "Em Andamento", now - timedelta(days=1))
        add_ticket("Concluida", category, requester, "Concluida", now - timedelta(days=1))
        db.session.commit()

        counts = dashboard_counts(Ticket.query, now)

        assert counts["abertas"] == 1
        assert counts["andamento"] == 1
        assert counts["concluidas"] == 1
        assert counts["vencem_hoje"] == 1
        assert counts["sem_responsavel"] == 2
        assert counts["atrasadas"] == 1


def test_dashboard_charts_exclude_finished_tickets(app):
    with app.app_context():
        profile = AccessProfile(name="Operacao")
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana2@example.com", password_hash="hash", profile_id=profile.id)
        assignee = User(name="Beto", email="beto@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add_all([requester, assignee])
        db.session.flush()

        now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        add_ticket("Aberta", category, requester, "Aberta", now + timedelta(days=1))
        add_ticket("Andamento", category, requester, "Em Andamento", now + timedelta(days=2), assignee.id)
        add_ticket("Pausada", category, requester, "Pausada", now + timedelta(days=3), assignee.id)
        add_ticket("Concluida", category, requester, "Concluida", now - timedelta(days=1), assignee.id)
        add_ticket("Cancelada", category, requester, "Cancelada", now - timedelta(days=1), assignee.id)
        db.session.commit()

        charts = dashboard_charts(Ticket.query)

        assert charts["pending_by_requester"]["total"] == 3
        assert charts["pending_by_requester"]["rows"][0]["label"] == "Ana"
        assert charts["active_workload"]["total"] == 2
        assert charts["active_workload"]["rows"][0]["label"] == "Beto"


def test_dashboard_response_disables_browser_cache(app):
    with app.test_request_context("/dashboard"):
        response = dashboard_response(
            counts={
                "atrasadas": 0,
                "abertas": 0,
                "andamento": 0,
                "pausadas": 0,
                "concluidas": 0,
            },
            charts={
                "pending_by_requester": {"total": 0, "rows": [], "gradient": "#e5e7eb 0% 100%"},
                "active_workload": {"total": 0, "rows": [], "gradient": "#e5e7eb 0% 100%"},
            },
            recent=[],
            now=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
        )

        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["Pragma"] == "no-cache"
        assert response.headers["Expires"] == "0"
