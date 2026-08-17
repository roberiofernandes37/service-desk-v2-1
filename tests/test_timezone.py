from datetime import datetime, timezone

import pytest

from app.services.timezone import format_datetime, local_day_bounds_utc, validate_timezone


def test_format_datetime_converts_utc_to_fortaleza():
    value = datetime(2026, 8, 17, 17, 0, tzinfo=timezone.utc)

    assert format_datetime(value, timezone_name="America/Fortaleza") == "17/08/2026 14:00"


def test_local_day_bounds_follow_configured_timezone():
    now = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)

    start, end = local_day_bounds_utc(now, "America/Fortaleza")

    assert start == datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)


def test_validate_timezone_rejects_unknown_zone():
    with pytest.raises(ValueError, match="Fuso horário inválido"):
        validate_timezone("America/TimezoneInexistente")
