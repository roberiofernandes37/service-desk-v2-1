from io import BytesIO
from pathlib import Path

import pytest
from werkzeug.exceptions import NotFound

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, Ticket, TicketAttachment, User


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestingConfig)
    app.config["UPLOAD_ROOT"] = str(tmp_path)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_multiple_initial_attachments_are_saved_shown_downloadable_and_removable(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante", can_work_tickets=True)
        user = User(name="Ana", email="ana@example.com", password_hash="hash", profile=profile)
        category = Category(name="GERAL")
        db.session.add_all([profile, user, category])
        db.session.commit()
        user_id = user.id
        category_id = category.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True

    response = client.post(
        "/solicitacoes/nova",
        data={
            "title": "Demanda com anexo",
            "description": "Descricao da demanda",
            "priority": "Media",
            "category_id": str(category_id),
            "branch_id": "0",
            "due_at": "2026-08-25",
            "initial_files": [
                (BytesIO(b"conteudo do anexo"), "evidencias.txt"),
                (BytesIO(b"segunda evidencia"), "segunda-evidencia.txt"),
            ],
        },
    )

    assert response.status_code == 302
    with app.app_context():
        ticket = Ticket.query.one()
        attachments = TicketAttachment.query.filter_by(ticket_id=ticket.id, deleted_at=None).order_by(TicketAttachment.id).all()
        assert len(attachments) == 2
        assert ticket.initial_file == attachments[0].stored_path
        assert all(Path(app.config["UPLOAD_ROOT"], attachment.stored_path).is_file() for attachment in attachments)
        ticket_id = ticket.id
        first_attachment_id = attachments[0].id
        stored_name = attachments[0].original_name
        second_attachment_id = attachments[1].id
        second_stored_path = attachments[1].stored_path

    detail = client.get(f"/solicitacoes/{ticket_id}")
    assert detail.status_code == 200
    assert b"Anexos" in detail.data
    assert stored_name.encode() in detail.data
    assert b"segunda-evidencia.txt" in detail.data
    assert b"Baixar" in detail.data

    dashboard = client.get("/dashboard")
    requests = client.get("/solicitacoes/")
    assert b"attachment-indicator" in dashboard.data
    assert b"attachment-indicator" in requests.data

    download = client.get(f"/solicitacoes/{ticket_id}/arquivo/inicial")
    assert download.status_code == 200
    assert download.data == b"conteudo do anexo"

    attachment_download = client.get(f"/solicitacoes/{ticket_id}/anexos/{second_attachment_id}")
    assert attachment_download.status_code == 200
    assert attachment_download.data == b"segunda evidencia"

    removed = client.post(f"/solicitacoes/{ticket_id}/anexos/{first_attachment_id}/excluir")
    assert removed.status_code == 302
    with app.app_context():
        deleted = db.session.get(TicketAttachment, first_attachment_id)
        assert deleted.deleted_at is not None
        assert Path(app.config["UPLOAD_ROOT"], second_stored_path).is_file()

    detail_after_delete = client.get(f"/solicitacoes/{ticket_id}")
    assert f'data-attachment-id="{first_attachment_id}"'.encode() not in detail_after_delete.data
    assert f'data-attachment-id="{second_attachment_id}"'.encode() in detail_after_delete.data
    with pytest.raises(NotFound):
        client.get(f"/solicitacoes/{ticket_id}/anexos/{first_attachment_id}")
    legacy_after_delete = client.get(f"/solicitacoes/{ticket_id}/arquivo/inicial")
    assert legacy_after_delete.status_code == 200
    assert legacy_after_delete.data == b"segunda evidencia"
