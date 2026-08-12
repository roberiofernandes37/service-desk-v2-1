from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..forms import LoginForm, ProfilePasswordForm
from ..models import User
from ..services.audit import audit

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
    if form.validate_on_submit():
        if not check_password_hash(current_user.password_hash, form.current_password.data):
            flash("Senha atual incorreta.", "danger")
        else:
            current_user.password_hash = generate_password_hash(form.new_password.data)
            audit("User", current_user.id, "password_changed")
            db.session.commit()
            flash("Senha alterada com sucesso.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/profile.html", form=form)
