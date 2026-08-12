from datetime import datetime, timezone

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
        raise BadRequest("Transicao de status invalida.")

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
            ticket.pause_started_at = None
        ticket.status = IN_PROGRESS
    elif action == "concluir":
        ticket.status = DONE
        ticket.resolution_note = note
        if final_file:
            ticket.final_file = final_file
    elif action == "reabrir":
        ticket.status = IN_PROGRESS
    elif action == "cancelar":
        ticket.status = CANCELED

    db.session.add(TicketHistory(ticket_id=ticket.id, user_id=user.id, action=action, note=note))
    audit("Ticket", ticket.id, action, before={"status": current_status}, after={"status": ticket.status})
