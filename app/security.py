from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user


def permission_required(permission_name):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login"))
            if not current_user.has_permission(permission_name):
                flash("Voce nao possui permissao para esta area.", "danger")
                return redirect(url_for("main.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def can_view_ticket(user, ticket):
    if not user or not user.is_authenticated:
        return False
    if user.has_permission("can_manage_settings") or user.has_permission("can_view_reports"):
        return True
    if user.has_permission("can_work_tickets"):
        return True
    return ticket.requester_id == user.id or ticket.assignee_id == user.id


def can_work_ticket(user, ticket):
    if not can_view_ticket(user, ticket):
        return False
    if user.has_permission("can_manage_settings"):
        return True
    return user.has_permission("can_work_tickets")


def can_cancel_ticket(user, ticket):
    if not can_view_ticket(user, ticket):
        return False
    return ticket.requester_id == user.id or user.has_permission("can_reset_data")


def assert_ticket_visible(ticket):
    if not can_view_ticket(current_user, ticket):
        abort(403)


def assert_ticket_action_allowed(ticket, action):
    assert_ticket_visible(ticket)
    if action in {"assumir", "pausar", "retomar", "concluir"} and not can_work_ticket(current_user, ticket):
        abort(403)
    if action in {"cancelar", "reabrir"} and not can_cancel_ticket(current_user, ticket):
        abort(403)
