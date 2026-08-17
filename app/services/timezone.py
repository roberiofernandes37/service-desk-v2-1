from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..extensions import db
from ..models import SystemConfig
from sqlalchemy.exc import SQLAlchemyError


DEFAULT_TIMEZONE = "America/Fortaleza"
TIMEZONE_CHOICES = [
    ("America/Fortaleza", "Fortaleza (UTC−03:00)"),
    ("America/Sao_Paulo", "São Paulo (UTC−03:00)"),
    ("America/Recife", "Recife (UTC−03:00)"),
    ("America/Bahia", "Salvador/Bahia (UTC−03:00)"),
    ("America/Belem", "Belém (UTC−03:00)"),
    ("America/Manaus", "Manaus (UTC−04:00)"),
    ("America/Boa_Vista", "Boa Vista (UTC−04:00)"),
    ("America/Rio_Branco", "Rio Branco (UTC−05:00)"),
    ("UTC", "UTC (sem conversão)"),
]


def timezone_choices():
    return TIMEZONE_CHOICES.copy()


def get_system_config():
    try:
        config = SystemConfig.query.first()
    except SQLAlchemyError:
        # Permite atualizar instalações existentes antes de executar init-db.
        db.session.rollback()
        SystemConfig.__table__.create(bind=db.engine, checkfirst=True)
        config = SystemConfig.query.first()
    if not config:
        config = SystemConfig(timezone=DEFAULT_TIMEZONE)
        db.session.add(config)
        db.session.commit()
    return config


def validate_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("Fuso horário inválido.")
    return value


def get_timezone_name():
    try:
        value = get_system_config().timezone
        validate_timezone(value)
        return value
    except Exception:
        return DEFAULT_TIMEZONE


def get_zoneinfo(timezone_name=None):
    return ZoneInfo(timezone_name or get_timezone_name())


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_local(value, timezone_name=None):
    value = as_utc(value)
    return value.astimezone(get_zoneinfo(timezone_name)) if value else None


def format_datetime(value, fmt="%d/%m/%Y %H:%M", timezone_name=None):
    local_value = to_local(value, timezone_name)
    return local_value.strftime(fmt) if local_value else "-"


def local_day_bounds_utc(now=None, timezone_name=None):
    now_utc = as_utc(now or datetime.now(timezone.utc))
    local_now = now_utc.astimezone(get_zoneinfo(timezone_name))
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def local_date_end_as_utc(value, timezone_name=None):
    local_zone = get_zoneinfo(timezone_name)
    return datetime.combine(value, time.max, tzinfo=local_zone).astimezone(timezone.utc)
