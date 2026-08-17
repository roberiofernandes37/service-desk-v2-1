from datetime import datetime, timedelta, timezone

from werkzeug.exceptions import BadRequest

from ..extensions import db
from ..models import TicketHistory
from .audit import audit

OPEN = "Aberta"
IN_PROGRESS = "Em Andamento"
PAUSED = "Pausada"
DONE = "Concluida"
CANCELED = "Cancelada"


def now_utc():
    return datetime.now(timezone.utc)


def active_seconds(ticket, timestamp=None):
    """Return elapsed working time, excluding time spent paused."""
    if not ticket or not ticket.created_at:
        return 0

    timestamp = timestamp or now_utc()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    started_at = ticket.created_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = max(int((timestamp - started_at).total_seconds()), 0)
    paused_seconds = int(ticket.total_paused_seconds or 0)
    if ticket.pause_started_at:
        pause_started_at = ticket.pause_started_at
        if pause_started_at.tzinfo is None:
            pause_started_at = pause_started_at.replace(tzinfo=timezone.utc)
        paused_seconds += max(int((timestamp - pause_started_at).total_seconds()), 0)
    return max(elapsed - paused_seconds, 0)


def apply_action(ticket, user, action, note=None, final_file=None):
    current_status = ticket.status
    valid = {
        OPEN: {"assumir", "cancelar"},
        IN_PROGRESS: {"pausar", "concluir", "cancelar"},
        PAUSED: {"retomar", "cancelar"},
        DONE: {"reabrir"},
        CANCELED: set(),
    }
    if action not in valid.get(current_status, set()):
        raise BadRequest("Transição de status inválida.")

    timestamp = now_utc()
    if action == "assumir":
        ticket.status = IN_PROGRESS
        ticket.assignee_id = user.id
    elif action == "pausar":
        ticket.status = PAUSED
        ticket.pause_started_at = timestamp
    elif action == "retomar":
        if ticket.pause_started_at:
            paused_seconds = int((timestamp - ticket.pause_started_at).total_seconds())
            ticket.total_paused_seconds += max(paused_seconds, 0)
            if ticket.due_at:
                ticket.due_at = ticket.due_at + timedelta(seconds=max(paused_seconds, 0))
            ticket.pause_started_at = None
        ticket.status = IN_PROGRESS
    elif action == "concluir":
        ticket.status = DONE
        ticket.resolution_note = note
        ticket.completed_at = timestamp
        ticket.resolved_by_id = user.id
        if final_file:
            ticket.final_file = final_file
    elif action == "reabrir":
        ticket.status = IN_PROGRESS
        ticket.completed_at = None
        ticket.resolved_by_id = None
    elif action == "cancelar":
        ticket.status = CANCELED

    db.session.add(TicketHistory(ticket_id=ticket.id, user_id=user.id, action=action, note=note))
    audit("Ticket", ticket.id, action, before={"status": current_status}, after={"status": ticket.status})
