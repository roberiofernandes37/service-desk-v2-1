import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Branch, Category, SystemConfig, User
from app.routes.admin import branch_name_exists, category_name_exists


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_category_name_duplicate_detection_allows_current_record(app):
    with app.app_context():
        first = Category(name="CADASTRO")
        second = Category(name="FINANCEIRO")
        db.session.add_all([first, second])
        db.session.flush()

        assert category_name_exists("CADASTRO")
        assert not category_name_exists("CADASTRO", ignore_id=first.id)
        assert category_name_exists("CADASTRO", ignore_id=second.id)


def test_branch_name_duplicate_detection_allows_current_record(app):
    with app.app_context():
        first = Branch(name="MATRIZ", kind="Loja")
        second = Branch(name="FILIAL 1", kind="Loja")
        db.session.add_all([first, second])
        db.session.flush()

        assert branch_name_exists("MATRIZ")
        assert not branch_name_exists("MATRIZ", ignore_id=first.id)
        assert branch_name_exists("MATRIZ", ignore_id=second.id)


def test_settings_persists_category_submitted_from_settings_form(app):
    with app.app_context():
        profile = AccessProfile(name="Administrador", can_manage_settings=True)
        admin = User(
            name="Admin",
            email="admin@example.com",
            password_hash="hash",
            profile=profile,
        )
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.post(
        "/admin/parametros",
        data={
            "category-name": "Financeiro",
            "category-active": "y",
            "category-submit": "Salvar categoria",
            "field_name[]": ["Centro de custo"],
            "field_type[]": ["text"],
            "field_required[]": ["0"],
        },
    )

    assert response.status_code == 302
    with app.app_context():
        category = Category.query.filter_by(name="FINANCEIRO").one()
        assert category.active
        assert [(field.name, field.field_type, field.required) for field in category.fields] == [
            ("CENTRO DE CUSTO", "text", True)
        ]


def test_settings_persists_system_timezone(app):
    with app.app_context():
        profile = AccessProfile(name="Administrador", can_manage_settings=True)
        admin = User(
            name="Admin",
            email="timezone@example.com",
            password_hash="hash",
            profile=profile,
        )
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.post(
        "/admin/parametros",
        data={
            "timezone-timezone": "America/Manaus",
            "timezone-submit": "Salvar fuso horário",
        },
    )

    assert response.status_code == 302
    with app.app_context():
        assert SystemConfig.query.one().timezone == "America/Manaus"
