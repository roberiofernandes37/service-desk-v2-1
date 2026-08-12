from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Branch, Category, Ticket, User
from app.routes.tickets import apply_ticket_filters, build_ticket_csv, order_tickets_query


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_ticket_filters_can_be_combined(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        category_a = Category(name="CADASTRO")
        category_b = Category(name="FINANCEIRO")
        db.session.add_all([profile, category_a, category_b])
        db.session.flush()

        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        assignee = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=profile.id)
        other_assignee = User(name="Caio", email="caio@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add_all([requester, assignee, other_assignee])
        db.session.flush()

        matching = Ticket(
            title="Cadastro de produtos",
            description="Criar novos itens",
            status="Em Andamento",
            priority="Urgente",
            category_id=category_a.id,
            requester_id=requester.id,
            assignee_id=assignee.id,
            due_at=datetime.now(timezone.utc),
        )
        ignored = Ticket(
            title="Cadastro financeiro",
            description="Outro responsavel",
            status="Em Andamento",
            priority="Urgente",
            category_id=category_b.id,
            requester_id=requester.id,
            assignee_id=other_assignee.id,
            due_at=datetime.now(timezone.utc),
        )
        db.session.add_all([matching, ignored])
        db.session.commit()

        filters = {
            "q": "produtos",
            "status": "Em Andamento",
            "priority": "Urgente",
            "due_state": "",
            "category_id": category_a.id,
            "branch_id": None,
            "requester_id": None,
            "assignee_id": assignee.id,
        }

        rows = apply_ticket_filters(Ticket.query, filters).all()

        assert rows == [matching]


def test_ticket_filters_support_unassigned_and_due_state(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        assignee = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add_all([requester, assignee])
        db.session.flush()

        now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        overdue_unassigned = Ticket(
            title="Atrasada sem responsavel",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=now - timedelta(hours=1),
        )
        overdue_assigned = Ticket(
            title="Atrasada com responsavel",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            assignee_id=assignee.id,
            due_at=now - timedelta(hours=1),
        )
        done_overdue = Ticket(
            title="Finalizada atrasada",
            description="Teste",
            status="Concluida",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=now - timedelta(hours=1),
        )
        db.session.add_all([overdue_unassigned, overdue_assigned, done_overdue])
        db.session.commit()

        filters = {
            "q": "",
            "status": "",
            "priority": "",
            "due_state": "overdue",
            "category_id": None,
            "branch_id": None,
            "requester_id": None,
            "assignee_id": -1,
        }

        rows = apply_ticket_filters(Ticket.query, filters, now=now).all()

        assert rows == [overdue_unassigned]


def test_ticket_filters_support_branch_and_general_branch(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        category = Category(name="GERAL")
        branch = Branch(name="MATRIZ", kind="Loja")
        db.session.add_all([profile, category, branch])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(requester)
        db.session.flush()

        branch_ticket = Ticket(
            title="Com filial",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            branch_id=branch.id,
            requester_id=requester.id,
            due_at=datetime.now(timezone.utc),
        )
        general_ticket = Ticket(
            title="Geral",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=datetime.now(timezone.utc),
        )
        db.session.add_all([branch_ticket, general_ticket])
        db.session.commit()

        base_filters = {
            "q": "",
            "status": "",
            "priority": "",
            "due_state": "",
            "category_id": None,
            "assignee_id": None,
            "requester_id": None,
        }

        assert apply_ticket_filters(Ticket.query, {**base_filters, "branch_id": branch.id}).all() == [branch_ticket]
        assert apply_ticket_filters(Ticket.query, {**base_filters, "branch_id": -1}).all() == [general_ticket]


def test_ticket_filters_support_requester(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        ana = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        bia = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add_all([ana, bia])
        db.session.flush()

        ana_ticket = Ticket(
            title="Solicitacao Ana",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=ana.id,
            due_at=datetime.now(timezone.utc),
        )
        bia_ticket = Ticket(
            title="Solicitacao Bia",
            description="Teste",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=bia.id,
            due_at=datetime.now(timezone.utc),
        )
        db.session.add_all([ana_ticket, bia_ticket])
        db.session.commit()

        filters = {
            "q": "",
            "status": "",
            "priority": "",
            "due_state": "",
            "category_id": None,
            "branch_id": None,
            "requester_id": ana.id,
            "assignee_id": None,
        }

        assert apply_ticket_filters(Ticket.query, filters).all() == [ana_ticket]


def test_ticket_search_includes_category_fields(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        category = Category(name="CADASTRO")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(requester)
        db.session.flush()

        matching = Ticket(
            title="Ajuste operacional",
            description="Sem o codigo no texto principal",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=datetime.now(timezone.utc),
            custom_data={"Produto": "SKU-4455"},
        )
        ignored = Ticket(
            title="Ajuste operacional",
            description="Outro cadastro",
            status="Aberta",
            priority="Media",
            category_id=category.id,
            requester_id=requester.id,
            due_at=datetime.now(timezone.utc),
            custom_data={"Produto": "SKU-0000"},
        )
        db.session.add_all([matching, ignored])
        db.session.commit()

        filters = {
            "q": "4455",
            "status": "",
            "priority": "",
            "due_state": "",
            "category_id": None,
            "branch_id": None,
            "requester_id": None,
            "assignee_id": None,
        }

        assert apply_ticket_filters(Ticket.query, filters).all() == [matching]


def test_ticket_ordering_prioritizes_urgent_then_high(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        category = Category(name="GERAL")
        db.session.add_all([profile, category])
        db.session.flush()
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add(requester)
        db.session.flush()
        due_at = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)

        baixa = Ticket(
            title="Baixa",
            description="Teste",
            status="Aberta",
            priority="Baixa",
            category_id=category.id,
            requester_id=requester.id,
            due_at=due_at,
        )
        urgente = Ticket(
            title="Urgente",
            description="Teste",
            status="Aberta",
            priority="Urgente",
            category_id=category.id,
            requester_id=requester.id,
            due_at=due_at,
        )
        alta = Ticket(
            title="Alta",
            description="Teste",
            status="Aberta",
            priority="Alta",
            category_id=category.id,
            requester_id=requester.id,
            due_at=due_at,
        )
        db.session.add_all([baixa, urgente, alta])
        db.session.commit()

        rows = order_tickets_query(Ticket.query, "priority").all()

        assert rows == [urgente, alta, baixa]


def test_ticket_csv_includes_operational_fields(app):
    with app.app_context():
        profile = AccessProfile(name="Atendente", can_work_tickets=True)
        category = Category(name="CADASTRO")
        db.session.add_all([profile, category])
        db.session.flush()

        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=profile.id)
        assignee = User(name="Bia", email="bia@example.com", password_hash="hash", profile_id=profile.id)
        db.session.add_all([requester, assignee])
        db.session.flush()

        ticket = Ticket(
            title="Cadastro com SLA",
            description="Teste",
            status="Concluida",
            priority="Alta",
            category_id=category.id,
            requester_id=requester.id,
            assignee_id=assignee.id,
            due_at=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
            total_paused_seconds=7200,
            custom_data={"SKU": "ABC"},
        )
        db.session.add(ticket)
        db.session.commit()

        csv_text = build_ticket_csv([ticket])

        assert "SLA" in csv_text
        assert "Atualizado em" in csv_text
        assert "Tempo pausado" in csv_text
        assert "Finalizada" in csv_text
        assert "2h" in csv_text
        assert "SKU" in csv_text
        assert "ABC" in csv_text
