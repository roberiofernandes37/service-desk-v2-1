from datetime import datetime, timedelta, timezone

from app.services.sla import format_duration, sla_state


class TicketStub:
    def __init__(self, status, due_at):
        self.status = status
        self.due_at = due_at


def test_sla_state_marks_overdue_today_ok_and_done():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)

    assert sla_state(TicketStub("Aberta", now - timedelta(minutes=1)), now)["key"] == "overdue"
    assert sla_state(TicketStub("Aberta", now.replace(hour=23)), now)["key"] == "today"
    assert sla_state(TicketStub("Aberta", now + timedelta(days=1)), now)["key"] == "ok"
    assert sla_state(TicketStub("Concluida", now - timedelta(days=2)), now)["key"] == "done"


def test_sla_state_accepts_naive_datetimes():
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    due_at = datetime(2026, 8, 11, 23, 59)

    assert sla_state(TicketStub("Aberta", due_at), now)["key"] == "today"


def test_format_duration_compacts_seconds_for_display():
    assert format_duration(0) == "0min"
    assert format_duration(1800) == "30min"
    assert format_duration(7200) == "2h"
    assert format_duration(90000) == "1d 1h"
