from datetime import datetime, timezone
from io import BytesIO

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Branch, Category, DynamicField, Ticket, User
from app.routes.tickets import can_edit_ticket
from app.services.timezone import to_local


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


def test_open_ticket_owner_can_edit_all_request_data(app, tmp_path):
    app.config["UPLOAD_ROOT"] = str(tmp_path)
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        current_category = Category(name="GERAL")
        new_category = Category(name="PRODUTOS")
        field = DynamicField(category=new_category, name="SKU", field_type="text", required=True)
        branch = Branch(name="MATRIZ", kind="Loja")
        requester = User(name="Ana", email="ana@example.com", password_hash="hash", profile=profile)
        db.session.add_all([profile, current_category, new_category, field, branch, requester])
        db.session.flush()
        ticket = Ticket(
            title="Titulo antigo",
            description="Descricao antiga",
            priority="Baixa",
            category_id=current_category.id,
            requester_id=requester.id,
            due_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id
        requester_id = requester.id
        new_category_id = new_category.id
        branch_id = branch.id
        field_id = field.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(requester_id)
        session["_fresh"] = True

    open_detail = client.get(f"/solicitacoes/{ticket_id}")
    assert f"/solicitacoes/{ticket_id}/editar".encode() in open_detail.data

    response = client.post(
        f"/solicitacoes/{ticket_id}/editar",
        data={
            "title": "Titulo atualizado",
            "description": "Descricao atualizada",
            "priority": "Urgente",
            "category_id": str(new_category_id),
            "branch_id": str(branch_id),
            "due_at": "2026-09-01",
            f"dynamic_{field_id}": "SKU-123",
            "initial_files": (BytesIO(b"novo anexo"), "novo-anexo.txt"),
        },
    )

    assert response.status_code == 302
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.title == "Titulo atualizado"
        assert ticket.description == "Descricao atualizada"
        assert ticket.priority == "Urgente"
        assert ticket.category_id == new_category_id
        assert ticket.branch_id == branch_id
        assert to_local(ticket.due_at).date().isoformat() == "2026-09-01"
        assert ticket.custom_data == {"SKU": "SKU-123"}
        assert ticket.initial_file and ticket.initial_file.endswith("_novo-anexo.txt")

        ticket.status = "Em Andamento"
        db.session.commit()

    closed_for_edit = client.get(f"/solicitacoes/{ticket_id}")
    assert f"/solicitacoes/{ticket_id}/editar".encode() not in closed_for_edit.data
