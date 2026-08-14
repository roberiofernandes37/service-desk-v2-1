"""Diagnostica SMTP sem exibir a senha armazenada."""

import argparse
import smtplib
import socket
import sys
from email.message import EmailMessage
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.config import TestingConfig  # noqa: E402
from app.services.mail_service import load_mail_config, normalize_mail_config  # noqa: E402


def report(label, message, success=True):
    marker = "OK" if success else "FALHA"
    print(f"[{marker}] {label}: {message}")


def main():
    parser = argparse.ArgumentParser(description="Diagnostica a configuração SMTP do Service Desk.")
    parser.add_argument("--recipient", help="Destinatário do teste real.")
    parser.add_argument("--send-test", action="store_true", help="Envia um e-mail após autenticar.")
    args = parser.parse_args()

    app = create_app(TestingConfig)
    with app.app_context():
        raw_config = load_mail_config()
        if not raw_config:
            report("Configuração", "instance/mail_config.json não foi encontrado ou está vazio.", False)
            return 1

        try:
            config = normalize_mail_config(raw_config)
        except (TypeError, ValueError) as exc:
            report("Configuração", str(exc), False)
            return 1

        required = {
            "server": config["server"],
            "porta": config["port"],
            "usuário": config["username"],
            "remetente": config["sender"],
            "senha": "preenchida" if config["password"] else "vazia",
        }
        for label, value in required.items():
            report(label.capitalize(), str(value), bool(value) and value != "vazia")
        report("TLS/SSL", f"TLS={config['use_tls']} SSL={config['use_ssl']}", not (config["use_tls"] and config["use_ssl"]))

        if not config["server"] or not config["username"] or not config["password"]:
            report("Pré-requisitos", "servidor, usuário e senha são obrigatórios para autenticar.", False)
            return 1

        try:
            addresses = socket.getaddrinfo(config["server"], config["port"], type=socket.SOCK_STREAM)
            ips = sorted({address[4][0] for address in addresses})
            report("DNS", ", ".join(ips))
        except OSError as exc:
            report("DNS", str(exc), False)
            return 1

        smtp = None
        try:
            if config["use_ssl"]:
                smtp = smtplib.SMTP_SSL(config["server"], config["port"], timeout=15)
                report("Conexão TCP/SSL", f"{config['server']}:{config['port']}")
            else:
                smtp = smtplib.SMTP(config["server"], config["port"], timeout=15)
                report("Conexão TCP", f"{config['server']}:{config['port']}")

            smtp.ehlo()
            if config["use_tls"]:
                smtp.starttls()
                smtp.ehlo()
                report("STARTTLS", "negociação concluída")

            smtp.login(config["username"], config["password"])
            report("Autenticação SMTP", "usuário e senha aceitos")

            if args.send_test:
                recipient = args.recipient or raw_config.get("test_recipient") or config["sender"]
                message = EmailMessage()
                message["Subject"] = "Teste SMTP - Service Desk V2.1"
                message["From"] = config["sender"]
                message["To"] = recipient
                message.set_content("Este é um teste de envio do Service Desk V2.1.")
                smtp.send_message(message)
                report("Envio", f"mensagem enviada para {recipient}")
        except smtplib.SMTPAuthenticationError:
            report("Autenticação SMTP", "credenciais recusadas; no Gmail, use uma senha de aplicativo.", False)
            return 1
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError, OSError) as exc:
            report("SMTP", f"{type(exc).__name__}: {exc}", False)
            return 1
        except smtplib.SMTPException as exc:
            report("SMTP", f"{type(exc).__name__}: {exc}", False)
            return 1
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except smtplib.SMTPException:
                    smtp.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
