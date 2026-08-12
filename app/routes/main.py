from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, abort, jsonify, make_response, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text
from sqlalchemy.orm import aliased

from ..extensions import db
from ..models import Notification, Ticket, User, utc_now
from ..services.ticket_workflow import CANCELED, DONE, IN_PROGRESS, OPEN, PAUSED

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard"))


@bp.route("/health")
def health():
    db.session.execute(text("select 1"))
    return jsonify(status="ok")


def dashboard_counts(query, now=None):
    now = now or datetime.now(timezone.utc)
    today_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)
    active_filter = Ticket.status.notin_([DONE, CANCELED])
    return {
        "ativas": query.filter(active_filter).count(),
        "abertas": query.filter_by(status=OPEN).count(),
        "andamento": query.filter_by(status=IN_PROGRESS).count(),
        "pausadas": query.filter_by(status=PAUSED).count(),
        "concluidas": query.filter_by(status=DONE).count(),
        "canceladas": query.filter_by(status=CANCELED).count(),
        "vencem_hoje": query.filter(active_filter, Ticket.due_at >= today_start, Ticket.due_at < tomorrow_start).count(),
        "sem_responsavel": query.filter(active_filter, Ticket.assignee_id.is_(None)).count(),
        "atrasadas": query.filter(active_filter, Ticket.due_at < now).count(),
    }


def _chart_colors():
    return ["#2563eb", "#3b82f6", "#60a5fa", "#17bfd8", "#0f9960", "#ffc20a", "#df3147"]


def _rows_to_chart(rows):
    colors = _chart_colors()
    total = sum(count for _, count in rows)
    max_count = max((count for _, count in rows), default=0)
    rows_data = []
    gradient_parts = []
    cursor = 0
    for index, (label, count) in enumerate(rows):
        color = colors[index % len(colors)]
        percent = round((count / total) * 100, 1) if total else 0
        bar_percent = round((count / max_count) * 100, 1) if max_count else 0
        rows_data.append(
            {
                "label": label or "Sem nome",
                "count": count,
                "percent": percent,
                "bar_percent": bar_percent,
                "color": color,
            }
        )
        next_cursor = 100 if index == len(rows) - 1 else cursor + percent
        gradient_parts.append(f"{color} {cursor}% {next_cursor}%")
        cursor = next_cursor
    return {
        "rows": rows_data,
        "total": total,
        "gradient": ", ".join(gradient_parts) if gradient_parts else "#e5e7eb 0% 100%",
    }


def dashboard_charts(query):
    visible_tickets = query.with_entities(Ticket.id).subquery()
    requester = aliased(User)
    assignee = aliased(User)
    active_filter = Ticket.status.notin_([DONE, CANCELED])
    pending_by_requester = (
        db.session.query(requester.name, func.count(Ticket.id))
        .join(visible_tickets, Ticket.id == visible_tickets.c.id)
        .join(requester, Ticket.requester_id == requester.id)
        .filter(active_filter)
        .group_by(requester.name)
        .order_by(func.count(Ticket.id).desc(), requester.name.asc())
        .limit(7)
        .all()
    )
    active_workload = (
        db.session.query(assignee.name, func.count(Ticket.id))
        .join(visible_tickets, Ticket.id == visible_tickets.c.id)
        .join(assignee, Ticket.assignee_id == assignee.id)
        .filter(Ticket.status.in_([IN_PROGRESS, PAUSED]))
        .group_by(assignee.name)
        .order_by(func.count(Ticket.id).desc(), assignee.name.asc())
        .limit(7)
        .all()
    )
    return {
        "pending_by_requester": _rows_to_chart(pending_by_requester),
        "active_workload": _rows_to_chart(active_workload),
    }


def dashboard_response(**context):
    response = make_response(render_template("dashboard.html", **context))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@bp.route("/dashboard")
@login_required
def dashboard():
    now = datetime.now(timezone.utc)
    query = Ticket.query
    if not (
        current_user.has_permission("can_manage_settings")
        or current_user.has_permission("can_view_reports")
        or current_user.has_permission("can_work_tickets")
    ):
        query = query.filter(or_(Ticket.requester_id == current_user.id, Ticket.assignee_id == current_user.id))

    counts = dashboard_counts(query, now)
    charts = dashboard_charts(query)
    recent = query.order_by(Ticket.created_at.desc()).limit(20).all()
    return dashboard_response(counts=counts, charts=charts, recent=recent, now=now)


@bp.route("/notificacoes")
@login_required
def notifications():
    rows = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("notifications.html", notifications=rows)


@bp.route("/notificacoes/<int:notification_id>/ler", methods=["POST"])
@login_required
def read_notification(notification_id):
    notification = db.get_or_404(Notification, notification_id)
    if notification.user_id != current_user.id:
        abort(403)
    notification.read_at = utc_now()
    db.session.commit()
    if notification.ticket_id:
        return redirect(url_for("tickets.detail", ticket_id=notification.ticket_id))
    return redirect(url_for("main.notifications"))


@bp.route("/notificacoes/limpar", methods=["POST"])
@login_required
def read_all_notifications():
    Notification.query.filter_by(user_id=current_user.id, read_at=None).update({"read_at": utc_now()})
    db.session.commit()
    return redirect(url_for("main.notifications"))
