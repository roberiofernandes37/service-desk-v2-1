import json
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models import BackupConfig, BackupRun


def utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def get_backup_config():
    config = BackupConfig.query.first()
    if not config:
        config = BackupConfig()
        db.session.add(config)
        db.session.commit()
    return config


def backup_root():
    root = Path(current_app.config["BACKUP_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_command(command):
    return subprocess.run(command, check=True, capture_output=True, text=True)


def database_url():
    return current_app.config["SQLALCHEMY_DATABASE_URI"]


def dump_database(target_sql):
    uri = database_url()
    if uri.startswith("postgresql"):
        run_command(
            [
                "pg_dump",
                f"--dbname={uri}",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                f"--file={target_sql}",
            ]
        )
        return
    if uri.startswith("sqlite:///"):
        sqlite_path = uri.replace("sqlite:///", "", 1)
        if sqlite_path.startswith("../"):
            sqlite_path = str(Path(current_app.instance_path) / sqlite_path.replace("../instance/", "", 1))
        connection = sqlite3.connect(sqlite_path)
        with target_sql.open("w", encoding="utf-8") as file:
            for line in connection.iterdump():
                file.write(f"{line}\n")
        connection.close()
        return
    raise RuntimeError("Tipo de banco não suportado para backup automático.")


def restore_database(source_sql):
    uri = database_url()
    if uri.startswith("postgresql"):
        run_command(["psql", f"--dbname={uri}", f"--file={source_sql}"])
        return
    if uri.startswith("sqlite:///"):
        sqlite_path = uri.replace("sqlite:///", "", 1)
        if sqlite_path.startswith("../"):
            sqlite_path = str(Path(current_app.instance_path) / sqlite_path.replace("../instance/", "", 1))
        connection = sqlite3.connect(sqlite_path)
        script = source_sql.read_text(encoding="utf-8")
        connection.executescript(script)
        connection.commit()
        connection.close()
        return
    raise RuntimeError("Tipo de banco não suportado para restauração automática.")


def add_directory(archive, path, arcname):
    source = Path(path)
    if source.exists():
        archive.add(source, arcname=arcname)


def create_backup(user_id=None, include_uploads=True, include_logs=True, mode="manual"):
    root = backup_root()
    run = BackupRun(
        user_id=user_id,
        action="backup",
        status="running",
        include_uploads=include_uploads,
        include_logs=include_logs,
        message=f"Backup {mode} iniciado.",
    )
    db.session.add(run)
    db.session.commit()

    file_name = f"service_desk_v2_1_backup_{utc_stamp()}.tar.gz"
    output_path = root / file_name
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            sql_path = temp_path / "database.sql"
            dump_database(sql_path)
            metadata = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "include_uploads": include_uploads,
                "include_logs": include_logs,
                "database": "database.sql",
            }
            (temp_path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            with tarfile.open(output_path, "w:gz") as archive:
                archive.add(sql_path, arcname="database.sql")
                archive.add(temp_path / "metadata.json", arcname="metadata.json")
                if include_uploads:
                    add_directory(archive, current_app.config["UPLOAD_ROOT"], "uploads")
                if include_logs:
                    add_directory(archive, current_app.config["LOG_ROOT"], "logs")
        run.status = "success"
        run.file_name = file_name
        run.file_path = str(output_path)
        run.size_bytes = output_path.stat().st_size
        run.message = "Backup concluido com sucesso."
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        prune_backups(exclude_id=run.id)
        return run
    except Exception as exc:
        db.session.rollback()
        run = db.session.get(BackupRun, run.id)
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.now(timezone.utc)
        db.session.commit()
        raise


def safe_extract(archive, destination):
    destination = Path(destination).resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if not str(target).startswith(str(destination)):
            raise RuntimeError("Arquivo de backup contém caminho inválido.")
    archive.extractall(destination)


def validate_backup_file(path):
    source = Path(path or "")
    if not source.exists():
        raise RuntimeError("Arquivo de backup não encontrado.")
    if source.stat().st_size <= 0:
        raise RuntimeError("Arquivo de backup esta vazio.")
    if not tarfile.is_tarfile(source):
        raise RuntimeError("Arquivo não é um pacote de backup válido.")

    with tarfile.open(source, "r:gz") as archive:
        names = set(archive.getnames())
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError("Arquivo de backup contém caminho inválido.")
        if "database.sql" not in names:
            raise RuntimeError("Backup sem database.sql.")
        if "metadata.json" not in names:
            raise RuntimeError("Backup sem metadata.json.")
        metadata_file = archive.extractfile("metadata.json")
        if not metadata_file:
            raise RuntimeError("Não foi possível ler metadata.json.")
        metadata = json.loads(metadata_file.read().decode("utf-8"))
        if metadata.get("database") != "database.sql":
            raise RuntimeError("Metadata do backup está inconsistente.")
    return True


def validate_backup_run(backup_run):
    if not backup_run or backup_run.action != "backup" or backup_run.status != "success":
        raise RuntimeError("Registro de backup inválido para validação.")
    validate_backup_file(backup_run.file_path)
    backup_run.message = "Backup testado com sucesso."
    db.session.commit()
    return backup_run


def restore_backup(backup_run, user_id=None):
    source = Path(backup_run.file_path or "")
    validate_backup_file(source)
    run = BackupRun(
        user_id=user_id,
        action="restore",
        status="running",
        file_name=backup_run.file_name,
        file_path=str(source),
        include_uploads=backup_run.include_uploads,
        include_logs=backup_run.include_logs,
        message="Restauração iniciada.",
    )
    db.session.add(run)
    db.session.commit()
    run_id = run.id
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with tarfile.open(source, "r:gz") as archive:
                safe_extract(archive, temp_path)
            restore_database(temp_path / "database.sql")
            uploads_path = temp_path / "uploads"
            if uploads_path.exists():
                shutil.copytree(uploads_path, current_app.config["UPLOAD_ROOT"], dirs_exist_ok=True)
            logs_path = temp_path / "logs"
            if logs_path.exists():
                shutil.copytree(logs_path, current_app.config["LOG_ROOT"], dirs_exist_ok=True)
        try:
            db.session.rollback()
            record = db.session.get(BackupRun, run_id)
            if record:
                record.status = "success"
                record.message = "Restauração concluída."
                record.finished_at = datetime.now(timezone.utc)
                db.session.commit()
                return record
        except Exception:
            db.session.rollback()
        run.status = "success"
        run.message = "Restauração concluída."
        run.finished_at = datetime.now(timezone.utc)
        return run
    except Exception as exc:
        db.session.rollback()
        record = db.session.get(BackupRun, run_id)
        if record:
            record.status = "failed"
            record.error_message = str(exc)
            record.finished_at = datetime.now(timezone.utc)
            db.session.commit()
        raise


def prune_backups(exclude_id=None):
    config = get_backup_config()
    max_count = max(config.max_backup_count or 1, 1)
    rows = (
        BackupRun.query.filter_by(action="backup", status="success")
        .order_by(BackupRun.created_at.desc())
        .all()
    )
    for row in rows[max_count:]:
        if exclude_id and row.id == exclude_id:
            continue
        if row.file_path:
            path = Path(row.file_path)
            if path.exists():
                path.unlink()
        row.status = "pruned"
        row.message = "Removido pela politica de retencao."
    db.session.commit()
