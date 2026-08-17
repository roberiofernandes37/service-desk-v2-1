import json
import sys
import tarfile

import pytest

from app import create_app
from app.config import TestingConfig
from app.extensions import db
from app.models import BackupConfig, BackupRun
from app.routes.admin import backup_status_label, format_bytes, schedule_times_valid
from app.services import backup as backup_service
from app.services.backup import create_backup, get_backup_config, validate_backup_file


@pytest.fixture()
def app():
    app = create_app(TestingConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_backup_config_defaults_are_created(app):
    with app.app_context():
        config = get_backup_config()

        assert config.enabled
        assert config.schedule_times == "12:00,18:00"
        assert config.max_backup_count == 14
        assert config.include_uploads
        assert config.include_logs
        assert BackupConfig.query.count() == 1


def test_backup_schedule_validation():
    assert schedule_times_valid("12:00,18:30")
    assert not schedule_times_valid("")
    assert not schedule_times_valid("25:00")
    assert not schedule_times_valid("meio-dia")


def test_backup_format_helpers():
    assert format_bytes(None) == "-"
    assert format_bytes(900) == "900 B"
    assert format_bytes(2048) == "2.0 KB"
    assert backup_status_label("success") == "Concluído"
    assert backup_status_label("failed") == "Falhou"


def test_backup_file_validation(tmp_path):
    valid_path = tmp_path / "backup.tar.gz"
    database_path = tmp_path / "database.sql"
    metadata_path = tmp_path / "metadata.json"
    database_path.write_text("select 1;", encoding="utf-8")
    metadata_path.write_text(json.dumps({"database": "database.sql"}), encoding="utf-8")
    with tarfile.open(valid_path, "w:gz") as archive:
        archive.add(database_path, arcname="database.sql")
        archive.add(metadata_path, arcname="metadata.json")

    assert validate_backup_file(valid_path)

    invalid_path = tmp_path / "invalid.tar.gz"
    with tarfile.open(invalid_path, "w:gz") as archive:
        archive.add(metadata_path, arcname="metadata.json")

    with pytest.raises(RuntimeError, match="database.sql"):
        validate_backup_file(invalid_path)


def test_backup_persists_file_identity_before_dump(app, tmp_path, monkeypatch):
    with app.app_context():
        app.config["BACKUP_ROOT"] = str(tmp_path / "backups")
        observed = {}

        def fake_dump(target_sql):
            current = BackupRun.query.order_by(BackupRun.id.desc()).first()
            observed["file_name"] = current.file_name
            observed["file_path"] = current.file_path
            target_sql.write_text("select 1;", encoding="utf-8")

        monkeypatch.setattr(backup_service, "dump_database", fake_dump)
        run = create_backup(include_uploads=False, include_logs=False)

        assert observed["file_name"] == run.file_name
        assert observed["file_path"] == run.file_path
        assert run.status == "success"


def test_backup_command_exposes_stderr():
    with pytest.raises(RuntimeError, match="falhou.*detalhe do erro"):
        backup_service.run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('detalhe do erro'); sys.exit(3)"]
        )
