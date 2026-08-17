import json
import smtplib
import socket
from pathlib import Path

from flask import current_app

from ..extensions import mail


MAIL_KEYS = {
    "server",
    "port",
    "username",
    "password",
    "sender",
    "test_recipient",
    "use_tls",
    "use_ssl",
}


def mail_config_path(app=None):
    app = app or current_app
    configured_path = app.config.get("MAIL_CONFIG_PATH")
    return Path(configured_path) if configured_path else Path(app.instance_path) / "mail_config.json"


def load_mail_config(app=None):
    path = mail_config_path(app)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        current_app.logger.exception("Não foi possível ler a configuração SMTP.")
        return {}
    return {key: data.get(key) for key in MAIL_KEYS if key in data}


def normalize_mail_config(data):
    normalized = {key: data.get(key) for key in MAIL_KEYS}
    normalized["server"] = (normalized["server"] or "").strip()
    normalized["username"] = (normalized["username"] or "").strip()
    normalized["sender"] = (normalized["sender"] or normalized["username"] or "").strip()
    normalized["port"] = int(normalized["port"] or 587)
    normalized["password"] = normalized["password"] or ""
    normalized["use_tls"] = bool(normalized["use_tls"])
    normalized["use_ssl"] = bool(normalized["use_ssl"])
    if normalized["use_tls"] and normalized["use_ssl"]:
        raise ValueError("TLS e SSL não podem ser usados ao mesmo tempo.")
    return normalized


def save_mail_config(data):
    normalized = normalize_mail_config(data)
    path = mail_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(path)
    apply_mail_config(current_app, normalized, reinitialize=True)
    return normalized


def apply_mail_config(app, data=None, reinitialize=False):
    settings = data if data is not None else load_mail_config(app)
    app.config.update(
        MAIL_SERVER=settings.get("server", ""),
        MAIL_PORT=settings.get("port", 587),
        MAIL_USERNAME=settings.get("username", ""),
        MAIL_PASSWORD=settings.get("password", ""),
        MAIL_DEFAULT_SENDER=settings.get("sender", ""),
        MAIL_USE_TLS=settings.get("use_tls", False),
        MAIL_USE_SSL=settings.get("use_ssl", False),
    )
    if reinitialize and mail:
        mail.init_app(app)


def send_email_safely(recipient, subject, body, html=None, settings=None):
    sent, _error = test_email_connection(recipient, subject, body, html=html, settings=settings)
    return sent


def test_email_connection(recipient, subject, body, html=None, settings=None):
    if not recipient:
        return False, "Informe um destinatário para o teste."
    settings = normalize_mail_config(settings if settings is not None else load_mail_config())
    if not settings["server"] or not settings["sender"]:
        return False, "Servidor SMTP e remetente padrão são obrigatórios."
    if mail is None:
        message = "Flask-Mail não está instalado no ambiente atual. Reconstrua a imagem do Docker."
        current_app.logger.error(message)
        return False, message

    apply_mail_config(current_app, settings, reinitialize=True)
    try:
        from flask_mail import Message

        message = Message(
            subject=subject,
            recipients=[recipient],
            body=body,
            html=html,
            sender=settings["sender"],
        )
        mail.send(message)
        return True, None
    except smtplib.SMTPAuthenticationError:
        message = "Autenticação SMTP recusada. No Gmail, use uma senha de aplicativo de 16 caracteres."
        current_app.logger.exception("Autenticação SMTP recusada para %s.", recipient)
        return False, message
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError):
        message = "Não foi possível conectar ao servidor SMTP. Confira servidor, porta e TLS/SSL."
        current_app.logger.exception("Falha de conexão SMTP para %s.", recipient)
        return False, message
    except Exception:
        message = "O servidor SMTP rejeitou o teste. Confira os dados informados e as permissões da conta."
        current_app.logger.exception("Falha ao enviar e-mail para %s.", recipient)
        return False, message
