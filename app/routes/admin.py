import csv
import io
import json
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, flash, make_response, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..forms import AccessProfileForm, AdminPasswordResetForm, BackupRestoreForm, BackupSettingsForm, BranchForm, CategoryForm, MailSettingsForm, TimezoneSettingsForm, UserEditForm, UserForm
from ..models import AccessProfile, AuditLog, BackupConfig, BackupRun, Branch, Category, DynamicField, SystemErrorLog, Ticket, User
from ..security import permission_required
from ..services.audit import audit
from ..services.backup import create_backup, get_backup_config, restore_backup, validate_backup_run
from ..services.ticket_workflow import CANCELED, DONE
from ..services.mail_service import load_mail_config, normalize_mail_config, save_mail_config, test_email_connection
from ..services.timezone import format_datetime, get_system_config, local_day_bounds_utc, timezone_choices, validate_timezone

bp = Blueprint("admin", __name__)
AUDIT_PER_PAGE = 50
ERROR_PER_PAGE = 50
BACKUP_PER_PAGE = 30


def configure_user_form(form):
    form.profile_id.choices = [(p.id, p.name) for p in AccessProfile.query.order_by(AccessProfile.name)]


def is_changing_own_profile(actor, target_user, new_profile_id):
    return actor.id == target_user.id and target_user.profile_id != int(new_profile_id)


def removes_own_user_management(actor, profile, can_manage_users):
    return actor.profile_id == profile.id and not can_manage_users


def category_name_exists(name, ignore_id=None):
    query = Category.query.filter(Category.name == name)
    if ignore_id:
        query = query.filter(Category.id != ignore_id)
    return query.first() is not None


def branch_name_exists(name, ignore_id=None):
    query = Branch.query.filter(Branch.name == name)
    if ignore_id:
        query = query.filter(Branch.id != ignore_id)
    return query.first() is not None


def format_audit_payload(payload):
    if payload is None:
        return "-"
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_audit_csv(rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["ID", "Data", "Usuário", "Entidade", "ID da entidade", "Ação", "IP", "Antes", "Depois"])
    for log in rows:
        writer.writerow(
            [
                log.id,
                format_datetime(log.created_at, "%d/%m/%Y %H:%M:%S"),
                log.user.name if log.user else "",
                log.entity,
                log.entity_id if log.entity_id is not None else "",
                log.action,
                log.ip_address or "",
                json.dumps(log.before or {}, ensure_ascii=False),
                json.dumps(log.after or {}, ensure_ascii=False),
            ]
        )
    return buffer.getvalue()


def build_error_csv(rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["ID", "Data", "Resolvido", "Usuário", "Tipo", "Mensagem", "Rota", "Método", "Status", "IP", "Payload"])
    for log in rows:
        writer.writerow(
            [
                log.id,
                format_datetime(log.created_at, "%d/%m/%Y %H:%M:%S"),
                "Sim" if log.resolved else "Não",
                log.user_name or (log.user.name if log.user else ""),
                log.error_type,
                log.error_message,
                log.path or "",
                log.method or "",
                log.status_code or "",
                log.ip_address or "",
                json.dumps(log.request_payload or {}, ensure_ascii=False),
            ]
        )
    return buffer.getvalue()


def parse_positive_int(value):
    if not value:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def audit_filter_state(args):
    return {
        "entity": args.get("entity", "").strip(),
        "action": args.get("action", "").strip(),
        "user_id": parse_positive_int(args.get("user_id")),
        "entity_id": parse_positive_int(args.get("entity_id")),
    }


def error_filter_state(args):
    return {
        "q": args.get("q", "").strip(),
        "error_type": args.get("error_type", "").strip(),
        "user_id": parse_positive_int(args.get("user_id")),
        "resolved": args.get("resolved", ""),
    }


def apply_audit_filters(query, filters):
    if filters["entity"]:
        query = query.filter(AuditLog.entity == filters["entity"])
    if filters["action"]:
        query = query.filter(AuditLog.action == filters["action"])
    if filters["user_id"]:
        query = query.filter(AuditLog.user_id == filters["user_id"])
    if filters["entity_id"]:
        query = query.filter(AuditLog.entity_id == filters["entity_id"])
    return query


def apply_error_filters(query, filters):
    if filters["q"]:
        like = f"%{filters['q']}%"
        query = query.filter(
            or_(
                SystemErrorLog.error_message.ilike(like),
                SystemErrorLog.path.ilike(like),
                SystemErrorLog.endpoint.ilike(like),
                SystemErrorLog.user_name.ilike(like),
            )
        )
    if filters["error_type"]:
        query = query.filter(SystemErrorLog.error_type == filters["error_type"])
    if filters["user_id"]:
        query = query.filter(SystemErrorLog.user_id == filters["user_id"])
    if filters["resolved"] == "yes":
        query = query.filter(SystemErrorLog.resolved.is_(True))
    elif filters["resolved"] == "no":
        query = query.filter(SystemErrorLog.resolved.is_(False))
    return query


def audit_filter_options():
    return {
        "entities": [row[0] for row in db.session.query(AuditLog.entity).distinct().order_by(AuditLog.entity).all()],
        "actions": [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()],
        "users": User.query.order_by(User.name).all(),
    }


def error_filter_options():
    return {
        "error_types": [row[0] for row in db.session.query(SystemErrorLog.error_type).distinct().order_by(SystemErrorLog.error_type).all()],
        "users": User.query.order_by(User.name).all(),
    }


def schedule_times_valid(value):
    for item in [part.strip() for part in value.split(",") if part.strip()]:
        try:
            datetime.strptime(item, "%H:%M")
        except ValueError:
            return False
    return bool(value.strip())


def format_bytes(value):
    if value is None:
        return "-"
    size = float(value)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "-"


def backup_status_label(status):
    return {
        "running": "Em andamento",
        "success": "Concluído",
        "failed": "Falhou",
        "pruned": "Removido",
    }.get(status, status or "-")


def count_by(field):
    return dict(db.session.query(field, func.count(Ticket.id)).group_by(field).all())


def percentage(part, total):
    if not total:
        return 0
    return round((part / total) * 100, 1)


def build_report_metrics(now=None):
    now = now or datetime.now(timezone.utc)
    today_start, tomorrow_start = local_day_bounds_utc(now)
    active_filter = Ticket.status.notin_([DONE, CANCELED])

    total = Ticket.query.count()
    concluded = Ticket.query.filter_by(status=DONE).count()
    canceled = Ticket.query.filter_by(status=CANCELED).count()
    active = Ticket.query.filter(active_filter).count()
    overdue = Ticket.query.filter(active_filter, Ticket.due_at < now).count()
    due_today = Ticket.query.filter(active_filter, Ticket.due_at >= today_start, Ticket.due_at < tomorrow_start).count()

    by_category = (
        db.session.query(Category.name, func.count(Ticket.id))
        .join(Ticket, Ticket.category_id == Category.id)
        .group_by(Category.name)
        .order_by(func.count(Ticket.id).desc(), Category.name.asc())
        .limit(10)
        .all()
    )
    oldest_overdue = (
        Ticket.query.filter(active_filter, Ticket.due_at < now)
        .order_by(Ticket.due_at.asc(), Ticket.created_at.asc())
        .limit(25)
        .all()
    )

    return {
        "total": total,
        "active": active,
        "concluded": concluded,
        "canceled": canceled,
        "overdue": overdue,
        "due_today": due_today,
        "sla_on_time": total - overdue,
        "sla_percent": percentage(total - overdue, total),
        "overdue_percent": percentage(overdue, total),
        "by_status": count_by(Ticket.status),
        "by_priority": count_by(Ticket.priority),
        "by_category": by_category,
        "oldest_overdue": oldest_overdue,
    }


@bp.route("/usuarios", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def users():
    form = UserForm()
    profile_form = AccessProfileForm()
    configure_user_form(form)
    if form.validate_on_submit():
        if User.query.filter_by(email=form.email.data.lower()).first():
            flash("Este e-mail já está cadastrado.", "danger")
        elif not form.password.data:
            flash("Informe uma senha inicial para o novo usuário.", "danger")
        else:
            user = User(
                name=form.name.data.strip(),
                email=form.email.data.lower(),
                password_hash=generate_password_hash(form.password.data),
                profile_id=form.profile_id.data,
                active=form.active.data,
            )
            db.session.add(user)
            db.session.flush()
            audit("User", user.id, "created", after={"email": user.email})
            db.session.commit()
            flash("Usuário criado.", "success")
            return redirect(url_for("admin.users"))

    return render_template(
        "admin/users.html",
        form=form,
        profile_form=profile_form,
        users=User.query.order_by(User.name).all(),
        profiles=AccessProfile.query.order_by(AccessProfile.name).all(),
    )


@bp.route("/perfis", methods=["POST"])
@login_required
@permission_required("can_manage_users")
def profiles():
    form = AccessProfileForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if AccessProfile.query.filter_by(name=name).first():
            flash("Já existe um perfil com este nome.", "danger")
        else:
            profile = AccessProfile(
                name=name,
                can_manage_users=form.can_manage_users.data,
                can_manage_settings=form.can_manage_settings.data,
                can_reset_data=form.can_reset_data.data,
                can_work_tickets=form.can_work_tickets.data,
                can_view_reports=form.can_view_reports.data,
            )
            db.session.add(profile)
            db.session.flush()
            audit("AccessProfile", profile.id, "created", after={"name": profile.name})
            db.session.commit()
            flash("Perfil criado.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/perfis/<int:profile_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def edit_profile(profile_id):
    profile = db.get_or_404(AccessProfile, profile_id)
    form = AccessProfileForm(obj=profile)
    if form.validate_on_submit():
        name = form.name.data.strip()
        duplicate = AccessProfile.query.filter(AccessProfile.name == name, AccessProfile.id != profile.id).first()
        if duplicate:
            flash("Já existe um perfil com este nome.", "danger")
        elif removes_own_user_management(current_user, profile, form.can_manage_users.data):
            flash("Você não pode remover a permissão de gerir usuários do próprio perfil.", "danger")
        else:
            before = {
                "name": profile.name,
                "can_manage_users": profile.can_manage_users,
                "can_manage_settings": profile.can_manage_settings,
                "can_reset_data": profile.can_reset_data,
                "can_work_tickets": profile.can_work_tickets,
                "can_view_reports": profile.can_view_reports,
            }
            profile.name = name
            profile.can_manage_users = form.can_manage_users.data
            profile.can_manage_settings = form.can_manage_settings.data
            profile.can_reset_data = form.can_reset_data.data
            profile.can_work_tickets = form.can_work_tickets.data
            profile.can_view_reports = form.can_view_reports.data
            audit(
                "AccessProfile",
                profile.id,
                "updated",
                before=before,
                after={
                    "name": profile.name,
                    "can_manage_users": profile.can_manage_users,
                    "can_manage_settings": profile.can_manage_settings,
                    "can_reset_data": profile.can_reset_data,
                    "can_work_tickets": profile.can_work_tickets,
                    "can_view_reports": profile.can_view_reports,
                },
            )
            db.session.commit()
            flash("Perfil atualizado.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/profile_form.html", form=form, profile=profile)


@bp.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def edit_user(user_id):
    user = db.get_or_404(User, user_id)
    form = UserEditForm(obj=user)
    configure_user_form(form)
    if form.validate_on_submit():
        email = form.email.data.lower()
        existing_user = User.query.filter(User.email == email, User.id != user.id).first()
        if existing_user:
            flash("Este e-mail já está cadastrado para outro usuário.", "danger")
        elif user.id == current_user.id and not form.active.data:
            flash("Você não pode desativar o próprio usuário logado.", "danger")
        elif is_changing_own_profile(current_user, user, form.profile_id.data):
            flash("Você não pode alterar o próprio perfil de acesso.", "danger")
        else:
            before = {
                "name": user.name,
                "email": user.email,
                "profile_id": user.profile_id,
                "active": user.active,
            }
            user.name = form.name.data.strip()
            user.email = email
            user.profile_id = form.profile_id.data
            user.active = form.active.data
            audit(
                "User",
                user.id,
                "updated",
                before=before,
                after={
                    "name": user.name,
                    "email": user.email,
                    "profile_id": user.profile_id,
                    "active": user.active,
                },
            )
            db.session.commit()
            flash("Usuário atualizado.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, user=user)


@bp.route("/usuarios/<int:user_id>/senha", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def reset_user_password(user_id):
    user = db.get_or_404(User, user_id)
    form = AdminPasswordResetForm()
    if form.validate_on_submit():
        user.password_hash = generate_password_hash(form.new_password.data)
        audit("User", user.id, "password_reset")
        db.session.commit()
        flash("Senha redefinida.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/password_form.html", form=form, user=user)


@bp.route("/usuarios/<int:user_id>/toggle", methods=["POST"])
@login_required
@permission_required("can_manage_users")
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("Você não pode desativar o próprio usuário logado.", "danger")
        return redirect(url_for("admin.users"))
    user.active = not user.active
    audit("User", user.id, "activated" if user.active else "deactivated")
    db.session.commit()
    flash("Status do usuário atualizado.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/parametros", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_settings")
def settings():
    category_form = CategoryForm(prefix="category")
    branch_form = BranchForm(prefix="branch")
    system_config = get_system_config()
    timezone_form = TimezoneSettingsForm(prefix="timezone")
    timezone_form.timezone.choices = timezone_choices()
    if request.method == "GET":
        timezone_form.timezone.data = system_config.timezone

    if timezone_form.submit.data and timezone_form.validate_on_submit():
        try:
            timezone_name = validate_timezone(timezone_form.timezone.data)
            system_config.timezone = timezone_name
            audit("SystemConfig", system_config.id, "timezone_updated", after={"timezone": timezone_name})
            db.session.commit()
            flash("Fuso horário do sistema atualizado.", "success")
            return redirect(url_for("admin.settings"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    if category_form.submit.data and category_form.validate_on_submit():
        category_name = category_form.name.data.strip().upper()
        if category_name_exists(category_name):
            flash("Já existe uma categoria com este nome.", "danger")
            return redirect(url_for("admin.settings"))
        category = Category(name=category_name, active=category_form.active.data)
        db.session.add(category)
        db.session.flush()
        field_names = request.form.getlist("field_name[]")
        field_types = request.form.getlist("field_type[]")
        required_flags = set(request.form.getlist("field_required[]"))
        saved_fields = []
        for index, name in enumerate(field_names):
            clean_name = name.strip().upper()
            if not clean_name:
                continue
            field_type = field_types[index] if index < len(field_types) else "text"
            field = DynamicField(
                category_id=category.id,
                name=clean_name,
                field_type=field_type,
                required=str(index) in required_flags,
                sort_order=index,
            )
            db.session.add(field)
            saved_fields.append({"name": clean_name, "type": field_type, "required": field.required})
        audit("Category", category.id, "created", after={"name": category.name, "fields": saved_fields})
        db.session.commit()
        flash("Categoria criada.", "success")
        return redirect(url_for("admin.settings"))

    if branch_form.submit.data and branch_form.validate_on_submit():
        branch_name = branch_form.name.data.strip()
        if branch_name_exists(branch_name):
            flash("Já existe uma filial com este nome.", "danger")
            return redirect(url_for("admin.settings"))
        branch = Branch(name=branch_name, kind=branch_form.kind.data.strip(), active=branch_form.active.data)
        db.session.add(branch)
        db.session.flush()
        audit("Branch", branch.id, "created")
        db.session.commit()
        flash("Filial criada.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        category_form=category_form,
        branch_form=branch_form,
        timezone_form=timezone_form,
        system_config=system_config,
        categories=Category.query.order_by(Category.name).all(),
        branches=Branch.query.order_by(Branch.name).all(),
    )


@bp.route("/email", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_settings")
def email_settings():
    saved_settings = load_mail_config()
    form = MailSettingsForm(
        data={
            "server": saved_settings.get("server", ""),
            "port": saved_settings.get("port", 587),
            "username": saved_settings.get("username", ""),
            "sender": saved_settings.get("sender", ""),
            "test_recipient": saved_settings.get("test_recipient") or current_user.email,
            "use_tls": saved_settings.get("use_tls", False),
            "use_ssl": saved_settings.get("use_ssl", False),
        }
    )
    if form.validate_on_submit():
        candidate = dict(saved_settings)
        candidate.update(
            server=form.server.data,
            port=form.port.data,
            username=form.username.data,
            sender=form.sender.data,
            test_recipient=form.test_recipient.data,
            use_tls=form.use_tls.data,
            use_ssl=form.use_ssl.data,
        )
        if form.password.data:
            candidate["password"] = form.password.data
        try:
            candidate = normalize_mail_config(candidate)
            if form.test.data:
                recipient = form.test_recipient.data or current_user.email
                sent, error_message = test_email_connection(
                    recipient,
                    "Teste de e-mail - Service Desk V2.1",
                    "Este e-mail confirma que a configuração SMTP do Service Desk está funcionando.",
                    settings=candidate,
                )
                if not sent:
                    flash(
                        f"Teste de conexão não realizado: {error_message} A configuração anteriormente salva permanece preservada.",
                        "danger",
                    )
                    return render_template("admin/email_settings.html", form=form)
            save_mail_config(candidate)
            audit("MailSettings", None, "tested_and_saved" if form.test.data else "saved", after={"server": candidate["server"], "port": candidate["port"], "sender": candidate["sender"]})
            db.session.commit()
            flash("Configuração de e-mail salva com sucesso.", "success")
            return redirect(url_for("admin.email_settings"))
        except (ValueError, OSError) as exc:
            flash(f"Não foi possível salvar a configuração de e-mail: {exc}", "danger")
    return render_template("admin/email_settings.html", form=form)


@bp.route("/categorias/<int:category_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_settings")
def edit_category(category_id):
    category = db.get_or_404(Category, category_id)
    form = CategoryForm(obj=category)
    if form.validate_on_submit():
        name = form.name.data.strip().upper()
        if category_name_exists(name, ignore_id=category.id):
            flash("Já existe uma categoria com este nome.", "danger")
        else:
            before = {"name": category.name, "active": category.active}
            category.name = name
            category.active = form.active.data
            audit("Category", category.id, "updated", before=before, after={"name": category.name, "active": category.active})
            db.session.commit()
            flash("Categoria atualizada.", "success")
            return redirect(url_for("admin.settings"))
    return render_template("admin/category_form.html", form=form, category=category)


@bp.route("/categorias/<int:category_id>/toggle", methods=["POST"])
@login_required
@permission_required("can_manage_settings")
def toggle_category(category_id):
    category = db.get_or_404(Category, category_id)
    category.active = not category.active
    audit("Category", category.id, "activated" if category.active else "deactivated")
    db.session.commit()
    flash("Status da categoria atualizado.", "success")
    return redirect(url_for("admin.settings"))


@bp.route("/categorias/<int:category_id>/campos", methods=["POST"])
@login_required
@permission_required("can_manage_settings")
def update_category_fields(category_id):
    category = db.get_or_404(Category, category_id)
    field_ids = request.form.getlist("edit_field_id[]")
    field_names = request.form.getlist("edit_field_name[]")
    field_types = request.form.getlist("edit_field_type[]")
    required_flags = set(request.form.getlist("edit_field_required[]"))
    current_fields = {field.id: field for field in category.fields}
    saved_fields = []

    for index, name in enumerate(field_names):
        clean_name = name.strip().upper()
        if not clean_name:
            continue
        field_type = field_types[index] if index < len(field_types) else "text"
        field_id_text = field_ids[index] if index < len(field_ids) else ""

        if field_id_text.isdigit() and int(field_id_text) in current_fields:
            field = current_fields.pop(int(field_id_text))
            field.name = clean_name
            field.field_type = field_type
            field.required = str(index) in required_flags
            field.sort_order = index
        else:
            field = DynamicField(
                category_id=category.id,
                name=clean_name,
                field_type=field_type,
                required=str(index) in required_flags,
                sort_order=index,
            )
            db.session.add(field)
        saved_fields.append({"name": clean_name, "type": field_type, "required": field.required})

    for removed_field in current_fields.values():
        db.session.delete(removed_field)

    audit("Category", category.id, "fields_updated", after={"fields": saved_fields})
    db.session.commit()
    flash("Campos da categoria atualizados.", "success")
    return redirect(url_for("admin.settings"))


@bp.route("/filiais/<int:branch_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_settings")
def edit_branch(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    form = BranchForm(obj=branch)
    if form.validate_on_submit():
        name = form.name.data.strip()
        if branch_name_exists(name, ignore_id=branch.id):
            flash("Já existe uma filial com este nome.", "danger")
        else:
            before = {"name": branch.name, "kind": branch.kind, "active": branch.active}
            branch.name = name
            branch.kind = form.kind.data.strip()
            branch.active = form.active.data
            audit(
                "Branch",
                branch.id,
                "updated",
                before=before,
                after={"name": branch.name, "kind": branch.kind, "active": branch.active},
            )
            db.session.commit()
            flash("Filial atualizada.", "success")
            return redirect(url_for("admin.settings"))
    return render_template("admin/branch_form.html", form=form, branch=branch)


@bp.route("/filiais/<int:branch_id>/toggle", methods=["POST"])
@login_required
@permission_required("can_manage_settings")
def toggle_branch(branch_id):
    branch = db.get_or_404(Branch, branch_id)
    branch.active = not branch.active
    audit("Branch", branch.id, "activated" if branch.active else "deactivated")
    db.session.commit()
    flash("Status da filial atualizado.", "success")
    return redirect(url_for("admin.settings"))


@bp.route("/relatorios")
@login_required
@permission_required("can_view_reports")
def reports():
    metrics = build_report_metrics()
    return render_template("admin/reports.html", metrics=metrics)


@bp.route("/backup", methods=["GET", "POST"])
@login_required
@permission_required("can_reset_data")
def backups():
    config = get_backup_config()
    settings_form = BackupSettingsForm(obj=config)
    restore_form = BackupRestoreForm()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "settings":
            settings_form = BackupSettingsForm()
            if settings_form.validate_on_submit():
                if not schedule_times_valid(settings_form.schedule_times.data):
                    flash("Informe horários no formato HH:MM, separados por vírgula.", "danger")
                else:
                    config.enabled = settings_form.enabled.data
                    config.schedule_times = settings_form.schedule_times.data.strip()
                    config.max_backup_count = settings_form.max_backup_count.data
                    config.include_uploads = settings_form.include_uploads.data
                    config.include_logs = settings_form.include_logs.data
                    db.session.commit()
                    audit("BackupConfig", config.id, "updated")
                    flash("Configuração de backup salva.", "success")
                    return redirect(url_for("admin.backups"))
        elif action == "create":
            try:
                run = create_backup(
                    user_id=current_user.id,
                    include_uploads=config.include_uploads,
                    include_logs=config.include_logs,
                    mode="manual",
                )
                audit("BackupRun", run.id, "created", after={"file_name": run.file_name})
                flash(f"Backup criado: {run.file_name}.", "success")
            except Exception as exc:
                flash(f"Não foi possível criar o backup: {exc}", "danger")
            return redirect(url_for("admin.backups"))
        elif action == "restore":
            restore_form = BackupRestoreForm()
            if restore_form.validate_on_submit():
                if restore_form.confirmation.data != "RESTAURAR":
                    flash("Digite RESTAURAR para confirmar a restauração.", "danger")
                    return redirect(url_for("admin.backups"))
                source = db.session.get(BackupRun, int(restore_form.backup_id.data))
                if not source or source.action != "backup" or source.status != "success":
                    flash("Backup inválido para restauração.", "danger")
                    return redirect(url_for("admin.backups"))
                try:
                    create_backup(user_id=current_user.id, include_uploads=True, include_logs=True, mode="pre-restore")
                    restore_backup(source, user_id=current_user.id)
                    flash("Restauração concluída. Reabra o sistema se notar dados antigos na tela.", "success")
                except Exception as exc:
                    flash(f"Não foi possível restaurar o backup: {exc}", "danger")
            return redirect(url_for("admin.backups"))
        elif action == "test":
            backup_id = parse_positive_int(request.form.get("backup_id"))
            source = db.session.get(BackupRun, backup_id) if backup_id else None
            try:
                validate_backup_run(source)
                flash(f"Backup testado com sucesso: {source.file_name}.", "success")
            except Exception as exc:
                flash(f"Backup inválido: {exc}", "danger")
            return redirect(url_for("admin.backups"))

    page = max(parse_positive_int(request.args.get("page")) or 1, 1)
    pagination = BackupRun.query.order_by(BackupRun.created_at.desc()).paginate(page=page, per_page=BACKUP_PER_PAGE, error_out=False)
    return render_template(
        "admin/backups.html",
        config=config,
        settings_form=settings_form,
        restore_form=restore_form,
        pagination=pagination,
        backups=pagination.items,
        format_bytes=format_bytes,
        backup_status_label=backup_status_label,
    )


@bp.route("/backup/<int:backup_id>/baixar")
@login_required
@permission_required("can_reset_data")
def download_backup(backup_id):
    backup = db.get_or_404(BackupRun, backup_id)
    if backup.action != "backup" or backup.status != "success" or not backup.file_path:
        abort(404)
    return send_file(backup.file_path, as_attachment=True, download_name=backup.file_name)


@bp.route("/auditoria")
@login_required
@permission_required("can_view_reports")
def audit_logs():
    filters = audit_filter_state(request.args)
    page = max(parse_positive_int(request.args.get("page")) or 1, 1)
    query = apply_audit_filters(AuditLog.query, filters)
    pagination = query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=AUDIT_PER_PAGE, error_out=False)
    return render_template(
        "admin/audit.html",
        logs=pagination.items,
        pagination=pagination,
        filters=filters,
        **audit_filter_options(),
    )


@bp.route("/auditoria/exportar.csv")
@login_required
@permission_required("can_view_reports")
def export_audit_csv():
    filters = audit_filter_state(request.args)
    rows = apply_audit_filters(AuditLog.query, filters).order_by(AuditLog.created_at.desc()).limit(5000).all()
    response = make_response(build_audit_csv(rows))
    response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    response.headers["Content-Disposition"] = f"attachment; filename=auditoria_service_desk_v2_1_{format_datetime(datetime.now(timezone.utc), '%Y%m%d_%H%M%S')}.csv"
    return response


@bp.route("/erros")
@login_required
@permission_required("can_view_reports")
def error_logs():
    filters = error_filter_state(request.args)
    page = max(parse_positive_int(request.args.get("page")) or 1, 1)
    query = apply_error_filters(SystemErrorLog.query, filters)
    pagination = query.order_by(SystemErrorLog.created_at.desc()).paginate(page=page, per_page=ERROR_PER_PAGE, error_out=False)
    return render_template(
        "admin/errors.html",
        logs=pagination.items,
        pagination=pagination,
        filters=filters,
        **error_filter_options(),
    )


@bp.route("/erros/exportar.csv")
@login_required
@permission_required("can_view_reports")
def export_error_csv():
    filters = error_filter_state(request.args)
    rows = apply_error_filters(SystemErrorLog.query, filters).order_by(SystemErrorLog.created_at.desc()).limit(5000).all()
    response = make_response(build_error_csv(rows))
    response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    response.headers["Content-Disposition"] = f"attachment; filename=erros_service_desk_v2_1_{format_datetime(datetime.now(timezone.utc), '%Y%m%d_%H%M%S')}.csv"
    return response


@bp.route("/erros/<int:log_id>", methods=["GET", "POST"])
@login_required
@permission_required("can_view_reports")
def error_detail(log_id):
    log = db.get_or_404(SystemErrorLog, log_id)
    if request.method == "POST":
        log.resolved = request.form.get("resolved") == "yes"
        log.technical_note = request.form.get("technical_note", "").strip() or None
        db.session.commit()
        flash("Diagnóstico atualizado.", "success")
        return redirect(url_for("admin.error_detail", log_id=log.id))
    return render_template(
        "admin/error_detail.html",
        log=log,
        payload=format_audit_payload(log.request_payload),
        traceback_payload=log.traceback or "-",
    )


@bp.route("/auditoria/<int:log_id>")
@login_required
@permission_required("can_view_reports")
def audit_detail(log_id):
    log = db.get_or_404(AuditLog, log_id)
    return render_template(
        "admin/audit_detail.html",
        log=log,
        before_payload=format_audit_payload(log.before),
        after_payload=format_audit_payload(log.after),
    )
