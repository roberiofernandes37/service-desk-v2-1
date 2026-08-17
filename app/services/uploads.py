from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app, send_file
from werkzeug.exceptions import BadRequest, Forbidden, NotFound
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {
    "txt",
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "csv",
    "xlsx",
    "xls",
    "doc",
    "docx",
    "xml",
    "ppt",
    "pptx",
}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file_storage, ticket_id, label):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise BadRequest("Formato de arquivo não permitido.")

    original = secure_filename(file_storage.filename)
    now = datetime.now(timezone.utc)
    relative_dir = Path(str(now.year)) / f"{now.month:02d}"
    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    target_dir = root / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"REQ-{ticket_id}_{label}_{uuid4().hex[:10]}_{original}"
    target = target_dir / filename
    file_storage.save(target)
    return str(relative_dir / filename).replace("\\", "/")


def save_uploads(file_storages, ticket_id, label):
    saved = []
    for file_storage in file_storages or []:
        if not file_storage or not file_storage.filename:
            continue
        stored_path = save_upload(file_storage, ticket_id, label)
        saved.append((stored_path, secure_filename(file_storage.filename)))
    return saved


def send_protected_upload(relative_path):
    if not relative_path:
        raise NotFound()
    root = Path(current_app.config["UPLOAD_ROOT"]).resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents and target != root:
        raise Forbidden()
    if not target.exists() or not target.is_file():
        raise NotFound()
    return send_file(target, as_attachment=True)
