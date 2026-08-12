from flask import request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog


def audit(entity, entity_id, action, before=None, after=None):
    user_id = current_user.id if current_user.is_authenticated else None
    db.session.add(
        AuditLog(
            user_id=user_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
        )
    )
