import csv
import json
from pathlib import Path

from flask import Flask
import click
from dotenv import load_dotenv
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from .config import load_config
from .extensions import csrf, db, login_manager, migrate
from .models import AccessProfile, BackupRun, Category, Notification, SystemErrorLog, User
from .services.backup import create_backup, get_backup_config, restore_backup, validate_backup_run
from .services.error_logging import ERROR_LOG_FILENAME
from .services.sla import format_duration, sla_state


def create_app(config_object=None):
    load_dotenv()
    app = Flask(__name__)
    config_class = config_object or load_config()
    app.config.from_object(config_class)
    app.config.from_prefixed_env()
    config_class.init_app(app)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Faca login para continuar."
    login_manager.login_message_category = "warning"

    register_blueprints(app)
    register_commands(app)
    register_template_context(app)
    register_error_handlers(app)

    return app


def register_blueprints(app):
    from .routes.admin import bp as admin_bp
    from .routes.auth import bp as auth_bp
    from .routes.main import bp as main_bp
    from .routes.tickets import bp as tickets_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(tickets_bp, url_prefix="/solicitacoes")
    app.register_blueprint(admin_bp, url_prefix="/admin")


def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Create database tables and starter records."""
        db.create_all()
        if not AccessProfile.query.filter_by(name="Administrador").first():
            db.session.add(
                AccessProfile(
                    name="Administrador",
                    can_manage_users=True,
                    can_manage_settings=True,
                    can_reset_data=True,
                    can_work_tickets=True,
                    can_view_reports=True,
                )
            )
        if not AccessProfile.query.filter_by(name="Atendente").first():
            db.session.add(AccessProfile(name="Atendente", can_work_tickets=True))
        if not Category.query.first():
            db.session.add(Category(name="GERAL"))
        db.session.commit()
        print("Database initialized.")

    @app.cli.command("seed-admin")
    @click.option("--email", required=True, help="Admin login e-mail.")
    @click.option("--password", required=True, help="Admin initial password.")
    def seed_admin(email, password):
        """Create or update the first admin user."""
        profile = AccessProfile.query.filter_by(name="Administrador").first()
        if not profile:
            profile = AccessProfile(
                name="Administrador",
                can_manage_users=True,
                can_manage_settings=True,
                can_reset_data=True,
                can_work_tickets=True,
                can_view_reports=True,
            )
            db.session.add(profile)
            db.session.flush()

        user = User.query.filter_by(email=email.lower()).first()
        if user:
            user.password_hash = generate_password_hash(password)
            user.profile_id = profile.id
            user.active = True
        else:
            user = User(
                name="Administrador",
                email=email.lower(),
                password_hash=generate_password_hash(password),
                profile_id=profile.id,
            )
            db.session.add(user)

        db.session.commit()
        print(f"Admin ready: {email.lower()}")

    @app.cli.command("errors")
    @click.option("--limit", default=20, show_default=True, help="Quantidade de erros recentes.")
    @click.option("--source", type=click.Choice(["database", "file"]), default="database", show_default=True)
    @click.option("--all", "show_all", is_flag=True, help="Inclui erros ja resolvidos.")
    def list_errors(limit, source, show_all):
        """List recent system errors without opening the web interface."""
        if source == "file":
            for item in read_error_file_tail(app, limit):
                click.echo(format_error_line(item))
            return

        try:
            query = SystemErrorLog.query
            if not show_all:
                query = query.filter_by(resolved=False)
            rows = query.order_by(SystemErrorLog.created_at.desc()).limit(limit).all()
        except SQLAlchemyError as exc:
            db.session.rollback()
            click.echo(f"Nao foi possivel consultar o banco: {exc}")
            click.echo("Tentando ler o arquivo de fallback:")
            for item in read_error_file_tail(app, limit):
                click.echo(format_error_line(item))
            return

        if not rows:
            click.echo("Nenhum erro encontrado.")
            return
        for row in rows:
            status = "Resolvido" if row.resolved else "Pendente"
            click.echo(
                f"#{row.id} | {row.created_at:%d/%m/%Y %H:%M:%S} | {status} | "
                f"{row.user_name or '-'} | {row.error_type} | {row.path or '-'} | {row.error_message}"
            )

    @app.cli.command("errors-export")
    @click.option("--output", default=None, help="Arquivo CSV de saida. Padrao: pasta de logs.")
    @click.option("--all", "show_all", is_flag=True, help="Inclui erros ja resolvidos.")
    def export_errors(output, show_all):
        """Export system errors to CSV without opening the web interface."""
        output_path = Path(output or Path(app.config["LOG_ROOT"]) / "system_error_logs.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            query = SystemErrorLog.query
            if not show_all:
                query = query.filter_by(resolved=False)
            rows = query.order_by(SystemErrorLog.created_at.desc()).limit(10000).all()
        except SQLAlchemyError as exc:
            db.session.rollback()
            click.echo(f"Nao foi possivel exportar pelo banco: {exc}")
            click.echo(f"Consulte o arquivo de fallback em: {Path(app.config['LOG_ROOT']) / ERROR_LOG_FILENAME}")
            return

        with output_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["ID", "Data", "Resolvido", "Usuario", "Tipo", "Mensagem", "Rota", "Metodo", "Status", "IP", "Payload"])
            for row in rows:
                writer.writerow(
                    [
                        row.id,
                        row.created_at.strftime("%d/%m/%Y %H:%M:%S"),
                        "Sim" if row.resolved else "Nao",
                        row.user_name or (row.user.name if row.user else ""),
                        row.error_type,
                        row.error_message,
                        row.path or "",
                        row.method or "",
                        row.status_code or "",
                        row.ip_address or "",
                        json.dumps(row.request_payload or {}, ensure_ascii=False),
                    ]
                )
        click.echo(f"Arquivo gerado: {output_path}")

    @app.cli.command("backup-create")
    @click.option("--include-uploads/--no-uploads", default=None, help="Inclui anexos no backup.")
    @click.option("--include-logs/--no-logs", default=None, help="Inclui logs no backup.")
    def backup_create(include_uploads, include_logs):
        """Create a full backup from the terminal."""
        config = get_backup_config()
        run = create_backup(
            include_uploads=config.include_uploads if include_uploads is None else include_uploads,
            include_logs=config.include_logs if include_logs is None else include_logs,
            mode="terminal",
        )
        click.echo(f"Backup criado: #{run.id} {run.file_name} ({run.size_bytes or 0} bytes)")

    @app.cli.command("backup-list")
    @click.option("--limit", default=20, show_default=True)
    def backup_list(limit):
        """List recent backup and restore operations."""
        rows = BackupRun.query.order_by(BackupRun.created_at.desc()).limit(limit).all()
        if not rows:
            click.echo("Nenhum backup encontrado.")
            return
        for row in rows:
            click.echo(
                f"#{row.id} | {row.created_at:%d/%m/%Y %H:%M:%S} | {row.action} | "
                f"{row.status} | {row.file_name or '-'} | {row.message or row.error_message or '-'}"
            )

    @app.cli.command("backup-test")
    @click.option("--id", "backup_id", type=int, required=True, help="ID do backup no historico.")
    def backup_test(backup_id):
        """Validate backup integrity from the terminal."""
        source = db.session.get(BackupRun, backup_id)
        if not source:
            click.echo("Backup nao encontrado.")
            return
        try:
            validate_backup_run(source)
            click.echo(f"Backup #{source.id} valido: {source.file_name}")
        except Exception as exc:
            click.echo(f"Backup invalido: {exc}")

    @app.cli.command("backup-restore")
    @click.option("--id", "backup_id", type=int, help="ID do backup no historico.")
    @click.option("--file", "backup_file", help="Caminho de arquivo de backup.")
    @click.option("--confirm", help="Digite RESTAURAR para confirmar.")
    def backup_restore(backup_id, backup_file, confirm):
        """Restore a backup from the terminal."""
        if confirm != "RESTAURAR":
            click.echo("Restauracao cancelada. Use --confirm RESTAURAR.")
            return
        if backup_id:
            source = db.session.get(BackupRun, backup_id)
            if not source or source.action != "backup" or source.status != "success":
                click.echo("Backup informado nao foi encontrado ou nao esta valido.")
                return
        elif backup_file:
            path = Path(backup_file)
            source = BackupRun(
                action="backup",
                status="success",
                file_name=path.name,
                file_path=str(path),
                include_uploads=True,
                include_logs=True,
            )
        else:
            click.echo("Informe --id ou --file.")
            return
        click.echo("Criando backup de seguranca antes da restauracao...")
        create_backup(mode="pre-restore")
        restore = restore_backup(source)
        click.echo(f"Restauracao registrada: #{restore.id} {restore.status}")


def read_error_file_tail(app, limit):
    log_path = Path(app.config["LOG_ROOT"]) / ERROR_LOG_FILENAME
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            items.append({"created_at": "-", "error_type": "LogFile", "path": "-", "error_message": line})
    return items


def format_error_line(item):
    return (
        f"{item.get('created_at', '-')} | {item.get('user_name') or '-'} | "
        f"{item.get('error_type', '-')} | {item.get('path') or '-'} | {item.get('error_message', '-')}"
    )


def register_template_context(app):
    @app.context_processor
    def inject_notifications():
        if not current_user.is_authenticated:
            return {
                "unread_notifications": [],
                "unread_notification_count": 0,
                "sla_state": sla_state,
                "format_duration": format_duration,
            }
        try:
            unread = (
                Notification.query.filter_by(user_id=current_user.id, read_at=None)
                .order_by(Notification.created_at.desc())
                .limit(6)
                .all()
            )
        except SQLAlchemyError:
            db.session.rollback()
            unread = []
        return {
            "unread_notifications": unread,
            "unread_notification_count": len(unread),
            "sla_state": sla_state,
            "format_duration": format_duration,
        }


def register_error_handlers(app):
    @app.errorhandler(Exception)
    def capture_unhandled_error(exc):
        if app.testing:
            raise exc

        if isinstance(exc, HTTPException):
            if exc.code and exc.code >= 500:
                from .services.error_logging import log_exception

                log_exception(exc, status_code=exc.code)
            return exc

        from .services.error_logging import log_exception

        log_exception(exc, status_code=500)
        return "Erro interno registrado para diagnostico.", 500


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
