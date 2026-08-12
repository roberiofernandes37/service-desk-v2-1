import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import AccessProfile, User
from app.routes.admin import is_changing_own_profile, removes_own_user_management


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_admin_cannot_change_own_profile_detection(app):
    with app.app_context():
        admin_profile = AccessProfile(name="Administrador", can_manage_users=True)
        requester_profile = AccessProfile(name="Solicitante")
        db.session.add_all([admin_profile, requester_profile])
        db.session.flush()

        admin = User(name="Admin", email="admin@example.com", password_hash="hash", profile_id=admin_profile.id)
        other = User(name="Ana", email="ana@example.com", password_hash="hash", profile_id=requester_profile.id)
        db.session.add_all([admin, other])
        db.session.flush()

        assert is_changing_own_profile(admin, admin, requester_profile.id)
        assert not is_changing_own_profile(admin, admin, admin_profile.id)
        assert not is_changing_own_profile(admin, other, admin_profile.id)


def test_admin_cannot_remove_user_management_from_own_profile(app):
    with app.app_context():
        admin_profile = AccessProfile(name="Administrador", can_manage_users=True)
        other_profile = AccessProfile(name="Atendente", can_work_tickets=True)
        db.session.add_all([admin_profile, other_profile])
        db.session.flush()

        admin = User(name="Admin", email="admin@example.com", password_hash="hash", profile_id=admin_profile.id)
        db.session.add(admin)
        db.session.flush()

        assert removes_own_user_management(admin, admin_profile, False)
        assert not removes_own_user_management(admin, admin_profile, True)
        assert not removes_own_user_management(admin, other_profile, False)
