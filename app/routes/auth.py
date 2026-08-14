from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..forms import EmailNotificationPreferencesForm, LoginForm, ProfilePasswordForm
from ..models import User, UserNotificationPreference
from ..services.audit import audit
from ..services.notifications import DEFAULT_EMAIL_EVENTS

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and user.active and check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            audit("User", user.id, "login")
            db.session.commit()
            return redirect(url_for("main.dashboard"))
        flash("Credenciais invalidas ou usuario inativo.", "danger")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("User", current_user.id, "logout")
    db.session.commit()
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/perfil", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfilePasswordForm()
    notification_form = EmailNotificationPreferencesForm(data=notification_preferences_data())
    if form.validate_on_submit():
        if not check_password_hash(current_user.password_hash, form.current_password.data):
            flash("Senha atual incorreta.", "danger")
        else:
            current_user.password_hash = generate_password_hash(form.new_password.data)
            audit("User", current_user.id, "password_changed")
            db.session.commit()
            flash("Senha alterada com sucesso.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/profile.html", form=form, notification_form=notification_form)


def notification_preferences_data():
    preferences = current_user.notification_preferences
    selected_events = set(preferences.email_events or []) if preferences else set(DEFAULT_EMAIL_EVENTS)
    return {
        "email_enabled": bool(preferences and preferences.email_enabled),
        **{event: event in selected_events for event in DEFAULT_EMAIL_EVENTS},
    }


@bp.route("/perfil/notificacoes", methods=["POST"])
@login_required
def save_notification_preferences():
    form = EmailNotificationPreferencesForm()
    if form.validate_on_submit():
        preferences = current_user.notification_preferences
        if not preferences:
            preferences = UserNotificationPreference(user_id=current_user.id)
            db.session.add(preferences)
        preferences.email_enabled = form.email_enabled.data
        preferences.email_events = [event for event in DEFAULT_EMAIL_EVENTS if getattr(form, event).data]
        audit(
            "UserNotificationPreference",
            current_user.id,
            "updated",
            after={"email_enabled": preferences.email_enabled, "email_events": preferences.email_events},
        )
        db.session.commit()
        flash("Preferências de e-mail salvas.", "success")
    else:
        flash("Não foi possível salvar as preferências de e-mail.", "danger")
    return redirect(url_for("auth.profile"))
