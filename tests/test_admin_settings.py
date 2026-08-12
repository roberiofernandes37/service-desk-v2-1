import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import Branch, Category
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
