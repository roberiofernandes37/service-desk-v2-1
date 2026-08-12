from ..extensions import db
from ..models import Notification


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
