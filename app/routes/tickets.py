import csv
import io
import json
from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import Text, case, func, or_
from werkzeug.exceptions import BadRequest

from ..extensions import db
from ..forms import CommentForm, TicketActionForm, TicketForm, TicketTransferForm
from ..models import AccessProfile, Branch, Category, Ticket, TicketAttachment, TicketComment, TicketHistory, User, utc_now
from ..security import assert_ticket_action_allowed, assert_ticket_visible, can_cancel_ticket
from ..services.audit import audit
from ..services.error_logging import log_exception
from ..services.notifications import notify_many, send_event_emails
from ..services.sla import format_duration, sla_state
from ..services.ticket_workflow import active_seconds, apply_action
from ..services.uploads import save_uploads, send_protected_upload

bp = Blueprint("tickets", __name__)

STATUSES = ["Aberta", "Em Andamento", "Pausada", "Concluida", "Cancelada"]
PRIORITIES = ["Baixa", "Media", "Alta", "Urgente"]
DUE_STATES = [
    ("", "Todos prazos"),
    ("overdue", "Atrasadas"),
    ("today", "Vencem hoje"),
    ("future", "No prazo"),
]
SORT_OPTIONS = [
    ("recent", "Mais recentes"),
    ("due", "Prazo mais próximo"),
    ("oldest", "Mais antigas"),
    ("priority", "Prioridade"),
]
PER_PAGE = 25


def load_ticket_or_404(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    assert_ticket_visible(ticket)
    return ticket


def configure_ticket_form(form, include_category_id=None):
    categories = [
        category
        for category in Category.query.order_by(Category.name).all()
        if category.active or category.id == include_category_id
    ]
    form.category_id.choices = [(0, "Selecione uma categoria")] + [(category.id, category.name) for category in categories]
    form.branch_id.choices = [(0, "Geral")] + [(b.id, b.name) for b in Branch.query.filter_by(active=True).order_by(Branch.name)]


def configure_transfer_form(form):
    assignees = (
        User.query.join(AccessProfile)
        .filter(User.active.is_(True), AccessProfile.can_work_tickets.is_(True))
        .order_by(User.name)
        .all()
    )
    form.assignee_id.choices = [(user.id, user.name) for user in assignees]


def can_edit_ticket(user, ticket):
    return ticket.status == "Aberta" and (ticket.requester_id == user.id or user.has_permission("can_manage_settings"))


def can_transfer_ticket(user, ticket):
    return ticket.status == "Em Andamento" and (
        user.has_permission("can_manage_settings") or user.has_permission("can_work_tickets")
    )


def add_ticket_attachments(ticket, files, user, kind, label):
    attachments = []
    for stored_path, original_name in save_uploads(files, ticket.id, label):
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            stored_path=stored_path,
            original_name=original_name,
            kind=kind,
            uploaded_by_id=user.id,
        )
        db.session.add(attachment)
        attachments.append(attachment)
        if kind == "initial" and not ticket.initial_file:
            ticket.initial_file = stored_path
        if kind == "final" and not ticket.final_file:
            ticket.final_file = stored_path
    return attachments


def category_schema_payload(include_category_id=None, values=None):
    categories = [
        category
        for category in Category.query.order_by(Category.name).all()
        if category.active or category.id == include_category_id
    ]
    values = values or {}
    return {
        category.id: [
            {
                "id": field.id,
                "name": field.name,
                "type": field.field_type,
                "required": field.required,
                "value": values.get(field.name, ""),
            }
            for field in category.fields
        ]
        for category in categories
    }


def collect_custom_data(category):
    values = {}
    missing = []
    for field in category.fields:
        raw_value = request.form.get(f"dynamic_{field.id}", "").strip()
        if field.required and not raw_value:
            missing.append(field.name)
        if raw_value:
            values[field.name] = raw_value
    if missing:
        raise BadRequest(f"Preencha os campos obrigatórios: {', '.join(missing)}.")
    return values


def visible_tickets_query():
    query = Ticket.query
    if not (
        current_user.has_permission("can_manage_settings")
        or current_user.has_permission("can_view_reports")
        or current_user.has_permission("can_work_tickets")
    ):
        query = query.filter(or_(Ticket.requester_id == current_user.id, Ticket.assignee_id == current_user.id))
    return query


def parse_int_filter(value):
    if value in {None, ""}:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number == -1:
        return -1
    return number if number > 0 else None


def day_bounds(now=None):
    now = now or datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)
    return now, today_start, tomorrow_start


def ticket_filter_state(args):
    return {
        "q": args.get("q", "").strip(),
        "status": args.get("status", ""),
        "priority": args.get("priority", ""),
        "due_state": args.get("due_state", ""),
        "sort": args.get("sort", "recent"),
        "category_id": parse_int_filter(args.get("category_id")),
        "branch_id": parse_int_filter(args.get("branch_id")),
        "requester_id": parse_int_filter(args.get("requester_id")),
        "assignee_id": parse_int_filter(args.get("assignee_id")),
    }


def apply_ticket_filters(query, filters, now=None):
    if filters["status"]:
        query = query.filter_by(status=filters["status"])
    if filters["priority"]:
        query = query.filter_by(priority=filters["priority"])
    if filters["due_state"]:
        current_time, today_start, tomorrow_start = day_bounds(now)
        active_filter = Ticket.status.notin_(["Concluida", "Cancelada"])
        if filters["due_state"] == "overdue":
            query = query.filter(active_filter, Ticket.due_at < current_time)
        elif filters["due_state"] == "today":
            query = query.filter(active_filter, Ticket.due_at >= today_start, Ticket.due_at < tomorrow_start)
        elif filters["due_state"] == "future":
            query = query.filter(active_filter, Ticket.due_at >= tomorrow_start)
    if filters["category_id"]:
        query = query.filter_by(category_id=filters["category_id"])
    if filters["branch_id"] == -1:
        query = query.filter(Ticket.branch_id.is_(None))
    elif filters["branch_id"]:
        query = query.filter_by(branch_id=filters["branch_id"])
    if filters["requester_id"]:
        query = query.filter_by(requester_id=filters["requester_id"])
    if filters["assignee_id"] == -1:
        query = query.filter(Ticket.assignee_id.is_(None))
    elif filters["assignee_id"]:
        query = query.filter_by(assignee_id=filters["assignee_id"])
    if filters["q"]:
        like = f"%{filters['q']}%"
        query = query.filter(
            or_(
                Ticket.title.ilike(like),
                Ticket.description.ilike(like),
                Ticket.custom_data.cast(Text).ilike(like),
            )
        )
    return query


def order_tickets_query(query, sort_key):
    priority_rank = case(
        {"Urgente": 1, "Alta": 2, "Media": 3, "Baixa": 4},
        value=Ticket.priority,
        else_=9,
    )
    if sort_key == "due":
        return query.order_by(Ticket.due_at.asc(), priority_rank.asc(), Ticket.created_at.desc())
    if sort_key == "oldest":
        return query.order_by(Ticket.created_at.asc())
    if sort_key == "priority":
        return query.order_by(priority_rank.asc(), Ticket.due_at.asc(), Ticket.created_at.desc())
    return query.order_by(Ticket.created_at.desc())


def filtered_tickets_query():
    filters = ticket_filter_state(request.args)
    return apply_ticket_filters(visible_tickets_query(), filters), filters


def filter_options():
    return {
        "categories": Category.query.order_by(Category.name).all(),
        "branches": Branch.query.order_by(Branch.name).all(),
        "requesters": User.query.order_by(User.name).all(),
        "assignees": User.query.join(AccessProfile).filter(AccessProfile.can_work_tickets.is_(True)).order_by(User.name).all(),
    }


def build_ticket_csv(rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "ID",
            "Título",
            "Status",
            "SLA",
            "Prioridade",
            "Solicitante",
            "Responsável",
            "Categoria",
            "Filial",
            "Prazo",
            "Criado em",
            "Atualizado em",
            "Tempo pausado",
            "Campos dinâmicos",
        ]
    )
    for ticket in rows:
        sla = sla_state(ticket)
        writer.writerow(
            [
                ticket.id,
                ticket.title,
                ticket.status,
                sla["label"],
                ticket.priority,
                ticket.requester.name if ticket.requester else "",
                ticket.assignee.name if ticket.assignee else "",
                ticket.category.name if ticket.category else "",
                ticket.branch.name if ticket.branch else "Geral",
                ticket.due_at.strftime("%d/%m/%Y") if ticket.due_at else "",
                ticket.created_at.strftime("%d/%m/%Y %H:%M") if ticket.created_at else "",
                ticket.updated_at.strftime("%d/%m/%Y %H:%M") if ticket.updated_at else "",
                format_duration(ticket.total_paused_seconds),
                json.dumps(ticket.custom_data or {}, ensure_ascii=False),
            ]
        )
    return buffer.getvalue()


@bp.route("/")
@login_required
def list_tickets():
    query, filters = filtered_tickets_query()
    page = max(parse_int_filter(request.args.get("page")) or 1, 1)
    pagination = order_tickets_query(query, filters["sort"]).paginate(page=page, per_page=PER_PAGE, error_out=False)
    return render_template(
        "tickets/list.html",
        tickets=pagination.items,
        pagination=pagination,
        filters=filters,
        statuses=STATUSES,
        priorities=PRIORITIES,
        due_states=DUE_STATES,
        sort_options=SORT_OPTIONS,
        **filter_options(),
    )


@bp.route("/kanban")
@login_required
def kanban():
    query, filters = filtered_tickets_query()
    cutoff = utc_now() - timedelta(days=7)
    active_rows = order_tickets_query(query.filter(Ticket.status != "Concluida"), filters["sort"]).limit(300).all()
    concluded_rows = (
        order_tickets_query(
            query.filter(
                Ticket.status == "Concluida",
                func.coalesce(Ticket.completed_at, Ticket.updated_at, Ticket.created_at) >= cutoff,
            ),
            filters["sort"],
        )
        .limit(300)
        .all()
    )
    columns = {status_name: [] for status_name in STATUSES}
    for ticket in [*active_rows, *concluded_rows]:
        columns.setdefault(ticket.status, []).append(ticket)
    return render_template(
        "tickets/kanban.html",
        columns=columns,
        filters=filters,
        statuses=STATUSES,
        priorities=PRIORITIES,
        due_states=DUE_STATES,
        sort_options=SORT_OPTIONS,
        **filter_options(),
    )


@bp.route("/exportar.csv")
@login_required
def export_csv():
    query, filters = filtered_tickets_query()
    rows = order_tickets_query(query, filters["sort"]).limit(1000).all()
    csv_text = build_ticket_csv(rows)

    audit("Ticket", None, "export_csv", after={**filters, "count": len(rows)})
    db.session.commit()
    response = make_response(csv_text)
    response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    response.headers["Content-Disposition"] = f"attachment; filename=service_desk_v2_1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return response


@bp.route("/nova", methods=["GET", "POST"])
@login_required
def create_ticket():
    form = TicketForm()
    configure_ticket_form(form)
    if len(form.category_id.choices) <= 1:
        flash("Cadastre ao menos uma categoria ativa antes de abrir solicitações.", "warning")
        return redirect(url_for("admin.settings"))

    if form.validate_on_submit():
        category = db.session.get(Category, form.category_id.data)
        if not category or not category.active:
            flash("Categoria inválida ou inativa.", "danger")
            return render_template("tickets/form.html", form=form, title="Nova solicitação", schema_map=category_schema_payload())

        due_at = datetime.combine(form.due_at.data, time.max, tzinfo=timezone.utc)
        try:
            custom_data = collect_custom_data(category)
            ticket = Ticket(
                title=form.title.data.strip(),
                description=form.description.data.strip(),
                priority=form.priority.data,
                category_id=form.category_id.data,
                branch_id=form.branch_id.data or None,
                requester_id=current_user.id,
                due_at=due_at,
                custom_data=custom_data,
            )
            db.session.add(ticket)
            db.session.flush()
            add_ticket_attachments(ticket, form.initial_files.data, current_user, "initial", "INICIAL")
            db.session.add(TicketHistory(ticket_id=ticket.id, user_id=current_user.id, action="criada"))
            audit("Ticket", ticket.id, "created", after={"title": ticket.title})
            worker_ids = [
                row.id
                for row in User.query.join(AccessProfile)
                .filter(User.active.is_(True), AccessProfile.can_work_tickets.is_(True))
                .all()
            ]
            notify_many(
                worker_ids,
                "Nova solicitação aberta",
                f"#{ticket.id} - {ticket.title}",
                ticket_id=ticket.id,
                exclude_user_id=current_user.id,
            )
            db.session.commit()
            flash("Solicitação criada com sucesso.", "success")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
        except BadRequest as exc:
            db.session.rollback()
            flash(str(exc.description), "danger")
        except Exception as exc:
            db.session.rollback()
            log_exception(exc, status_code=500)
            flash(f"Não foi possível salvar a solicitação: {exc}", "danger")

    return render_template("tickets/form.html", form=form, title="Nova solicitação", schema_map=category_schema_payload())


@bp.route("/<int:ticket_id>/editar", methods=["GET", "POST"])
@login_required
def edit(ticket_id):
    ticket = load_ticket_or_404(ticket_id)
    if not can_edit_ticket(current_user, ticket):
        flash("Apenas solicitações abertas podem ser editadas pelo solicitante ou gestor.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    form = TicketForm(obj=ticket)
    configure_ticket_form(form, include_category_id=ticket.category_id)

    if request.method == "GET":
        form.category_id.data = ticket.category_id
        form.branch_id.data = ticket.branch_id or 0
        form.due_at.data = ticket.due_at.date()

    if form.validate_on_submit():
        category = db.session.get(Category, form.category_id.data)
        if not category or (not category.active and category.id != ticket.category_id):
            flash("Categoria inválida ou inativa.", "danger")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
        before = {
            "title": ticket.title,
            "priority": ticket.priority,
            "category_id": ticket.category_id,
            "branch_id": ticket.branch_id,
            "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
            "custom_data": ticket.custom_data or {},
            "initial_file": ticket.initial_file,
        }
        try:
            ticket.title = form.title.data.strip()
            ticket.description = form.description.data.strip()
            ticket.priority = form.priority.data
            ticket.category_id = category.id
            ticket.branch_id = form.branch_id.data or None
            ticket.due_at = datetime.combine(form.due_at.data, time.max, tzinfo=timezone.utc)
            ticket.custom_data = collect_custom_data(category)
            added_attachments = add_ticket_attachments(ticket, form.initial_files.data, current_user, "initial", "INICIAL")
            if added_attachments:
                db.session.add(
                    TicketHistory(
                        ticket_id=ticket.id,
                        user_id=current_user.id,
                        action="anexo_adicionado",
                        note=f"{len(added_attachments)} anexo(s) adicionado(s).",
                    )
                )
            db.session.add(TicketHistory(ticket_id=ticket.id, user_id=current_user.id, action="editada"))
            audit(
                "Ticket",
                ticket.id,
                "edited",
                before=before,
                after={
                    "title": ticket.title,
                    "priority": ticket.priority,
                    "category_id": ticket.category_id,
                    "branch_id": ticket.branch_id,
                    "due_at": ticket.due_at.isoformat(),
                    "custom_data": ticket.custom_data or {},
                    "initial_file": ticket.initial_file,
                },
            )
            notify_many(
                [ticket.requester_id, ticket.assignee_id],
                "Solicitação editada",
                f"#{ticket.id} foi editada por {current_user.name}.",
                ticket_id=ticket.id,
                exclude_user_id=current_user.id,
            )
            db.session.commit()
            flash("Solicitação atualizada com sucesso.", "success")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
        except BadRequest as exc:
            db.session.rollback()
            flash(str(exc.description), "danger")
        except Exception as exc:
            db.session.rollback()
            log_exception(exc, status_code=500)
            flash(f"Não foi possível editar a solicitação: {exc}", "danger")

    schema_map = category_schema_payload(include_category_id=ticket.category_id, values=ticket.custom_data or {})
    return render_template(
        "tickets/form.html",
        form=form,
        title=f"Editar solicitação #{ticket.id}",
        ticket=ticket,
        editing=True,
        schema_map=schema_map,
    )


@bp.route("/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = load_ticket_or_404(ticket_id)
    action_form = TicketActionForm()
    transfer_form = TicketTransferForm()
    configure_transfer_form(transfer_form)
    comment_form = CommentForm()
    return render_template(
        "tickets/detail.html",
        ticket=ticket,
        action_form=action_form,
        transfer_form=transfer_form,
        can_transfer=can_transfer_ticket(current_user, ticket),
        can_cancel=can_cancel_ticket(current_user, ticket),
        can_edit=can_edit_ticket(current_user, ticket),
        attachments=TicketAttachment.query.filter_by(ticket_id=ticket.id, deleted_at=None).all(),
        comment_form=comment_form,
        active_seconds=active_seconds,
    )


@bp.route("/<int:ticket_id>/acao", methods=["POST"])
@login_required
def action(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    form = TicketActionForm()
    if not form.validate_on_submit():
        flash("Formulário inválido. Recarregue a página e tente novamente.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    assert_ticket_action_allowed(ticket, form.action.data)
    try:
        if form.action.data in {"pausar", "concluir", "cancelar"} and not (form.note.data or "").strip():
            raise BadRequest("Informe uma observação para esta ação.")
        final_file = None
        if form.action.data == "concluir":
            final_attachments = add_ticket_attachments(ticket, form.final_files.data, current_user, "final", "FINAL")
            if final_attachments:
                final_file = final_attachments[0].stored_path
        apply_action(ticket, current_user, form.action.data, note=form.note.data, final_file=final_file)
        email_event = {
            "pausar": "pausada",
            "concluir": "concluida",
            "cancelar": "cancelada",
            "reabrir": "reaberta",
        }.get(form.action.data)
        recipients = [ticket.requester_id, ticket.assignee_id]
        notify_many(
            recipients,
            "Solicitação atualizada",
            f"#{ticket.id} foi alterada para {ticket.status} por {current_user.name}.",
            ticket_id=ticket.id,
            exclude_user_id=current_user.id,
        )
        db.session.commit()
        if email_event:
            send_event_emails(
                recipients,
                email_event,
                f"Service Desk: demanda #{ticket.id} atualizada",
                f"A demanda #{ticket.id} - {ticket.title} foi alterada para {ticket.status} por {current_user.name}.",
                ticket_id=ticket.id,
                exclude_user_id=current_user.id,
            )
        flash("Ação registrada com sucesso.", "success")
    except BadRequest as exc:
        db.session.rollback()
        flash(str(exc.description), "danger")
    except Exception as exc:
        db.session.rollback()
        log_exception(exc, status_code=500)
        flash(f"Não foi possível concluir a ação: {exc}", "danger")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@bp.route("/<int:ticket_id>/transferir", methods=["POST"])
@login_required
def transfer(ticket_id):
    ticket = load_ticket_or_404(ticket_id)
    if not can_transfer_ticket(current_user, ticket):
        abort(403)

    form = TicketTransferForm()
    configure_transfer_form(form)
    if not form.validate_on_submit():
        flash("Selecione um responsável válido.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    assignee = db.session.get(User, form.assignee_id.data)
    if not assignee or not assignee.active or not assignee.has_permission("can_work_tickets"):
        flash("Responsável inválido ou inativo.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    before = {"assignee_id": ticket.assignee_id, "status": ticket.status}
    previous_assignee_id = ticket.assignee_id
    ticket.assignee_id = assignee.id
    note = (form.note.data or "").strip() or f"Transferida para {assignee.name}."
    db.session.add(TicketHistory(ticket_id=ticket.id, user_id=current_user.id, action="transferida", note=note))
    audit(
        "Ticket",
        ticket.id,
        "transferred",
        before=before,
        after={"assignee_id": ticket.assignee_id, "status": ticket.status},
    )
    notify_many(
        [ticket.requester_id, previous_assignee_id, assignee.id],
        "Solicitação transferida",
        f"#{ticket.id} agora esta com {assignee.name}.",
        ticket_id=ticket.id,
        exclude_user_id=current_user.id,
    )
    db.session.commit()
    flash("Responsável atualizado.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@bp.route("/<int:ticket_id>/comentarios", methods=["POST"])
@login_required
def comment(ticket_id):
    ticket = load_ticket_or_404(ticket_id)
    form = CommentForm()
    if form.validate_on_submit():
        db.session.add(TicketComment(ticket_id=ticket.id, user_id=current_user.id, body=form.body.data.strip()))
        audit("TicketComment", ticket.id, "created")
        notify_many(
            [ticket.requester_id, ticket.assignee_id],
            "Novo comentário",
            f"{current_user.name} comentou na solicitação #{ticket.id}.",
            ticket_id=ticket.id,
            exclude_user_id=current_user.id,
        )
        db.session.commit()
        send_event_emails(
            [ticket.requester_id, ticket.assignee_id],
            "comentario",
            f"Service Desk: novo comentário na demanda #{ticket.id}",
            f"{current_user.name} adicionou um novo comentário na demanda #{ticket.id} - {ticket.title}.",
            ticket_id=ticket.id,
            exclude_user_id=current_user.id,
        )
        flash("Comentário enviado.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@bp.route("/<int:ticket_id>/arquivo/<kind>")
@login_required
def download(ticket_id, kind):
    ticket = load_ticket_or_404(ticket_id)
    if kind == "inicial":
        attachment_kind = "initial"
        legacy_path = ticket.initial_file
    elif kind == "final":
        attachment_kind = "final"
        legacy_path = ticket.final_file
    else:
        abort(404)
    attachments = TicketAttachment.query.filter_by(ticket_id=ticket.id, kind=attachment_kind).order_by(TicketAttachment.created_at.asc()).all()
    attachment = next((item for item in attachments if item.deleted_at is None), None)
    if attachment:
        return send_protected_upload(attachment.stored_path)
    if attachments:
        abort(404)
    path = legacy_path
    return send_protected_upload(path)


@bp.route("/<int:ticket_id>/anexos/<int:attachment_id>")
@login_required
def download_attachment(ticket_id, attachment_id):
    ticket = load_ticket_or_404(ticket_id)
    attachment = db.get_or_404(TicketAttachment, attachment_id)
    if attachment.ticket_id != ticket.id or attachment.deleted_at:
        abort(404)
    return send_protected_upload(attachment.stored_path)


@bp.route("/<int:ticket_id>/anexos/<int:attachment_id>/excluir", methods=["POST"])
@login_required
def delete_attachment(ticket_id, attachment_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not can_edit_ticket(current_user, ticket):
        abort(403)
    attachment = db.get_or_404(TicketAttachment, attachment_id)
    if attachment.ticket_id != ticket.id or attachment.deleted_at:
        abort(404)

    attachment.deleted_at = utc_now()
    attachment.deleted_by_id = current_user.id
    db.session.add(
        TicketHistory(
            ticket_id=ticket.id,
            user_id=current_user.id,
            action="anexo_excluido",
            note=f"Anexo removido: {attachment.original_name}",
        )
    )
    audit(
        "TicketAttachment",
        attachment.id,
        "deleted",
        before={"ticket_id": ticket.id, "original_name": attachment.original_name},
        after={"deleted": True},
    )
    db.session.commit()
    flash("Anexo removido da demanda.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))
