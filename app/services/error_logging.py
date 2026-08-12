import traceback
import json
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app, request
from flask_login import current_user

from ..extensions import db
from ..models import SystemErrorLog

SENSITIVE_TOKENS = ("password", "senha", "token", "secret", "csrf", "key")
ERROR_LOG_FILENAME = "service_desk_errors.log"


def mask_value(key, value):
    key_text = str(key).lower()
    if any(token in key_text for token in SENSITIVE_TOKENS):
        return "***"
    return value


def normalize_values(values):
    normalized = {}
    for key in values:
        all_values = values.getlist(key)
        if len(all_values) == 1:
            normalized[key] = mask_value(key, all_values[0])
        else:
            normalized[key] = [mask_value(key, item) for item in all_values]
    return normalized


def collect_request_payload():
    payload = {
        "args": normalize_values(request.args),
        "form": normalize_values(request.form),
        "files": {name: storage.filename for name, storage in request.files.items()},
    }
    if request.is_json:
        json_payload = request.get_json(silent=True)
        if isinstance(json_payload, dict):
            payload["json"] = {key: mask_value(key, value) for key, value in json_payload.items()}
        else:
            payload["json"] = json_payload
    return payload


def current_user_snapshot():
    if not current_user or not current_user.is_authenticated:
        return None, None
    return current_user.id, current_user.name


def error_snapshot(exc, status_code=None):
    user_id, user_name = current_user_snapshot()
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "user_name": user_name,
        "error_type": exc.__class__.__name__,
        "error_message": str(exc) or "Erro sem mensagem",
        "endpoint": request.endpoint,
        "path": request.path,
        "method": request.method,
        "status_code": status_code,
        "ip_address": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": (request.user_agent.string or "")[:300],
        "request_payload": collect_request_payload(),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:],
    }


def write_error_file(snapshot):
    try:
        log_root = Path(current_app.config.get("LOG_ROOT", "logs"))
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / ERROR_LOG_FILENAME
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(snapshot, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def log_exception(exc, status_code=None):
    snapshot = error_snapshot(exc, status_code=status_code)
    write_error_file(snapshot)
    log = SystemErrorLog(
        user_id=snapshot["user_id"],
        user_name=snapshot["user_name"],
        error_type=snapshot["error_type"],
        error_message=snapshot["error_message"],
        endpoint=snapshot["endpoint"],
        path=snapshot["path"],
        method=snapshot["method"],
        status_code=snapshot["status_code"],
        ip_address=snapshot["ip_address"],
        user_agent=snapshot["user_agent"],
        request_payload=snapshot["request_payload"],
        traceback=snapshot["traceback"],
    )
    try:
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()
    return log
