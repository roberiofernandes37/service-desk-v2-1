import csv
import io
import json
from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import Text, case, or_
from werkzeug.exceptions import BadRequest

from ..extensions import db
from ..forms import CommentForm, TicketActionForm, TicketEditForm, TicketForm, TicketTransferForm
from ..models import AccessProfile, Branch, Category, Ticket, TicketComment, TicketHistory, User
from ..security import assert_ticket_action_allowed, assert_ticket_visible
from ..services.audit import audit
from ..services.error_logging import log_exception
from ..services.notifications import notify_many
from ..services.sla import format_duration, sla_state
from ..services.ticket_workflow import apply_action
from ..services.uploads import save_upload, send_protected_upload

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
    ("due", "Prazo mais proximo"),
    ("oldest", "Mais antigas"),
    ("priority", "Prioridade"),
]
PER_PAGE = 25


def load_ticket_or_404(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    assert_ticket_visible(ticket)
    return ticket


def configure_ticket_form(form):
    form.category_id.choices = [(c.id, c.name) for c in Category.query.filter_by(active=True).order_by(Category.name)]
    form.branch_id.choices = [(0, "Geral")] + [(b.id, b.name) for b in Branch.query.filter_by(active=True).order_by(Branch.name)]


def configure_ticket_edit_form(form):
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
    return ticket.status not in {"Concluida", "Cancelada"} and (
        user.has_permission("can_manage_settings") or user.has_permission("can_work_tickets")
    )


def category_schema_payload():
    categories = Category.query.filter_by(active=True).order_by(Category.name).all()
    return {
        category.id: [
            {"id": field.id, "name": field.name, "type": field.field_type, "required": field.required}
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
        raise BadRequest(f"Preencha os campos obrigatorios: {', '.join(missing)}.")
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
            "Titulo",
            "Status",
            "SLA",
            "Prioridade",
            "Solicitante",
            "Responsavel",
            "Categoria",
            "Filial",
            "Prazo",
            "Criado em",
            "Atualizado em",
            "Tempo pausado",
            "Campos dinamicos",
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
    rows = order_tickets_query(query, filters["sort"]).limit(300).all()
    columns = {status_name: [] for status_name in STATUSES}
    for ticket in rows:
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
    if not form.category_id.choices:
        flash("Cadastre ao menos uma categoria ativa antes de abrir solicitacoes.", "warning")
        return redirect(url_for("admin.settings"))

    if form.validate_on_submit():
        category = db.session.get(Category, form.category_id.data)
        if not category or not category.active:
            flash("Categoria invalida ou inativa.", "danger")
            return render_template("tickets/form.html", form=form, title="Nova solicitacao", schema_map=category_schema_payload())

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
            ticket.initial_file = save_upload(form.initial_file.data, ticket.id, "INICIAL")
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
                "Nova solicitacao aberta",
                f"#{ticket.id} - {ticket.title}",
                ticket_id=ticket.id,
                exclude_user_id=current_user.id,
            )
            db.session.commit()
            flash("Solicitacao criada com sucesso.", "success")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
        except BadRequest as exc:
            db.session.rollback()
            flash(str(exc.description), "danger")
        except Exception as exc:
            db.session.rollback()
            log_exception(exc, status_code=500)
            flash(f"Nao foi possivel salvar a solicitacao: {exc}", "danger")

    return render_template("tickets/form.html", form=form, title="Nova solicitacao", schema_map=category_schema_payload())


@bp.route("/<int:ticket_id>/editar", methods=["GET", "POST"])
@login_required
def edit(ticket_id):
    ticket = load_ticket_or_404(ticket_id)
    if not can_edit_ticket(current_user, ticket):
        flash("Apenas solicitacoes abertas podem ser editadas pelo solicitante ou gestor.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    form = TicketEditForm(obj=ticket)
    configure_ticket_edit_form(form)

    if request.method == "GET":
        form.branch_id.data = ticket.branch_id or 0
        form.due_at.data = ticket.due_at.date()

    if form.validate_on_submit():
        before = {
            "title": ticket.title,
            "priority": ticket.priority,
            "branch_id": ticket.branch_id,
            "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
            "custom_data": ticket.custom_data or {},
        }
        try:
            ticket.title = form.title.data.strip()
            ticket.description = form.description.data.strip()
            ticket.priority = form.priority.data
            ticket.branch_id = form.branch_id.data or None
            ticket.due_at = datetime.combine(form.due_at.data, time.max, tzinfo=timezone.utc)
            ticket.custom_data = collect_custom_data(ticket.category)
            db.session.add(TicketHistory(ticket_id=ticket.id, user_id=current_user.id, action="editada"))
            audit(
                "Ticket",
                ticket.id,
                "edited",
                before=before,
                after={
                    "title": ticket.title,
                    "priority": ticket.priority,
                    "branch_id": ticket.branch_id,
                    "due_at": ticket.due_at.isoformat(),
                    "custom_data": ticket.custom_data or {},
                },
            )
            notify_many(
                [ticket.requester_id, ticket.assignee_id],
                "Solicitacao editada",
                f"#{ticket.id} foi editada por {current_user.name}.",
                ticket_id=ticket.id,
                exclude_user_id=current_user.id,
            )
            db.session.commit()
            flash("Solicitacao atualizada com sucesso.", "success")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
        except BadRequest as exc:
            db.session.rollback()
            flash(str(exc.description), "danger")
        except Exception as exc:
            db.session.rollback()
            log_exception(exc, status_code=500)
            flash(f"Nao foi possivel editar a solicitacao: {exc}", "danger")

    schema_map = {
        ticket.category_id: [
            {"id": field.id, "name": field.name, "type": field.field_type, "required": field.required, "value": (ticket.custom_data or {}).get(field.name, "")}
            for field in ticket.category.fields
        ]
    }
    return render_template(
        "tickets/form.html",
        form=form,
        title=f"Editar solicitacao #{ticket.id}",
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
        comment_form=comment_form,
    )


@bp.route("/<int:ticket_id>/acao", methods=["POST"])
@login_required
def action(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    form = TicketActionForm()
    if not form.validate_on_submit():
        flash("Formulario invalido. Recarregue a pagina e tente novamente.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    assert_ticket_action_allowed(ticket, form.action.data)
    try:
        if form.action.data in {"pausar", "concluir", "cancelar"} and not (form.note.data or "").strip():
            raise BadRequest("Informe uma observacao para esta acao.")
        final_file = None
        if form.action.data == "concluir":
            final_file = save_upload(form.final_file.data, ticket.id, "FINAL")
        apply_action(ticket, current_user, form.action.data, note=form.note.data, final_file=final_file)
        recipients = [ticket.requester_id, ticket.assignee_id]
        notify_many(
            recipients,
            "Solicitacao atualizada",
            f"#{ticket.id} foi alterada para {ticket.status} por {current_user.name}.",
            ticket_id=ticket.id,
            exclude_user_id=current_user.id,
        )
        db.session.commit()
        flash("Acao registrada com sucesso.", "success")
    except BadRequest as exc:
        db.session.rollback()
        flash(str(exc.description), "danger")
    except Exception as exc:
        db.session.rollback()
        log_exception(exc, status_code=500)
        flash(f"Nao foi possivel concluir a acao: {exc}", "danger")
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
        flash("Selecione um responsavel valido.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    assignee = db.session.get(User, form.assignee_id.data)
    if not assignee or not assignee.active or not assignee.has_permission("can_work_tickets"):
        flash("Responsavel invalido ou inativo.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))

    before = {"assignee_id": ticket.assignee_id, "status": ticket.status}
    previous_assignee_id = ticket.assignee_id
    ticket.assignee_id = assignee.id
    if ticket.status == "Aberta":
        ticket.status = "Em Andamento"
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
        "Solicitacao transferida",
        f"#{ticket.id} agora esta com {assignee.name}.",
        ticket_id=ticket.id,
        exclude_user_id=current_user.id,
    )
    db.session.commit()
    flash("Responsavel atualizado.", "success")
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
            "Novo comentario",
            f"{current_user.name} comentou na solicitacao #{ticket.id}.",
            ticket_id=ticket.id,
            exclude_user_id=current_user.id,
        )
        db.session.commit()
        flash("Comentario enviado.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@bp.route("/<int:ticket_id>/arquivo/<kind>")
@login_required
def download(ticket_id, kind):
    ticket = load_ticket_or_404(ticket_id)
    if kind == "inicial":
        path = ticket.initial_file
    elif kind == "final":
        path = ticket.final_file
    else:
        abort(404)
    return send_protected_upload(path)
