import pytest
from werkzeug.exceptions import BadRequest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, Category, DynamicField
from app.routes.tickets import collect_custom_data


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_required_dynamic_field_is_validated_server_side(app):
    with app.app_context():
        profile = AccessProfile(name="Solicitante")
        category = Category(name="CADASTRO")
        db.session.add_all([profile, category])
        db.session.flush()
        db.session.add(DynamicField(category_id=category.id, name="SKU", field_type="text", required=True))
        db.session.commit()

        with app.test_request_context("/solicitacoes/nova", method="POST", data={}):
            with pytest.raises(BadRequest):
                collect_custom_data(category)

        with app.test_request_context("/solicitacoes/nova", method="POST", data={f"dynamic_{category.fields[0].id}": "ABC-123"}):
            assert collect_custom_data(category) == {"SKU": "ABC-123"}
