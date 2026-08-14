from ..extensions import db
from ..models import Notification, User
from .mail_service import send_email_safely


EMAIL_NOTIFICATION_EVENTS = {
    "pausada": "Demanda pausada",
    "concluida": "Demanda concluída",
    "reaberta": "Demanda reaberta",
    "cancelada": "Demanda cancelada",
    "comentario": "Novo comentário",
}
DEFAULT_EMAIL_EVENTS = tuple(EMAIL_NOTIFICATION_EVENTS)


def notify_user(user_id, title, message, ticket_id=None):
    if not user_id:
        return None
    notification = Notification(user_id=user_id, ticket_id=ticket_id, title=title, message=message)
    db.session.add(notification)
    return notification


def notify_many(user_ids, title, message, ticket_id=None, exclude_user_id=None):
    seen = set()
    for user_id in user_ids:
        if not user_id or user_id == exclude_user_id or user_id in seen:
            continue
        seen.add(user_id)
        notify_user(user_id, title, message, ticket_id=ticket_id)


def send_event_emails(user_ids, event, subject, body, ticket_id=None, exclude_user_id=None):
    """Send optional e-mail notifications without allowing SMTP failures to stop the workflow."""
    if event not in EMAIL_NOTIFICATION_EVENTS:
        return

    recipient_ids = {
        user_id
        for user_id in user_ids
        if user_id and user_id != exclude_user_id
    }
    if not recipient_ids:
        return

    users = User.query.filter(User.id.in_(recipient_ids), User.active.is_(True)).all()
    for user in users:
        preferences = user.notification_preferences
        if not preferences or not preferences.email_enabled:
            continue
        if event not in (preferences.email_events or []):
            continue
        send_email_safely(
            user.email,
            subject,
            body,
        )
