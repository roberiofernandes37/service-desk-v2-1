from datetime import datetime, timezone

FINAL_STATUSES = {"Concluida", "Cancelada"}


def normalize_datetime(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def sla_state(ticket, now=None):
    if not ticket or not ticket.due_at:
        return {"key": "none", "label": "-", "is_active": False}
    if ticket.status in FINAL_STATUSES:
        return {"key": "done", "label": "Finalizada", "is_active": False}
    if ticket.status == "Pausada":
        return {"key": "paused", "label": "Pausada", "is_active": False}

    now = normalize_datetime(now or datetime.now(timezone.utc))
    due_at = normalize_datetime(ticket.due_at)
    if due_at < now:
        return {"key": "overdue", "label": "Atrasada", "is_active": True}
    if due_at.date() == now.date():
        return {"key": "today", "label": "Vence hoje", "is_active": True}
    return {"key": "ok", "label": "No prazo", "is_active": True}


def format_duration(seconds):
    seconds = max(int(seconds or 0), 0)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}min")
    return " ".join(parts)
