"""Tests for domain/content_calendar.py (plan 33): pure rules, no database.
Run with pytest, or directly: python3 tests/test_content_calendar_domain.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import content_calendar as K  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)   # a Wednesday


def h(hours):
    return NOW + timedelta(hours=hours)


def test_states():
    assert K.state(None, False, NOW) == "unplanned"
    assert K.state(h(5), False, NOW) == "planned"
    assert K.state(h(-1), False, NOW) == "overdue"
    assert K.state(NOW, False, NOW) == "overdue"
    assert K.state(h(-1), True, NOW) == "published" and K.state(None, True, NOW) == "published"


def test_reminders_the_day_before_and_when_due_and_never_for_old_or_linked_drafts():
    assert K.reminder(h(30), False, NOW) is None
    assert K.reminder(h(24), False, NOW) == "soon" and K.reminder(h(1), False, NOW) == "soon"
    assert K.reminder(NOW, False, NOW) == "due" and K.reminder(h(-23), False, NOW) == "due"
    assert K.reminder(h(-25), False, NOW) is None
    assert K.reminder(h(1), True, NOW) is None and K.reminder(None, False, NOW) is None


def test_fits_a_best_slot_by_utc_weekday_and_hour():
    slots = [{"weekday": "Wed", "hour": 12}, {"weekday": "Fri", "hour": 18}]
    assert K.fits(NOW, slots) is True
    assert K.fits(h(1), slots) is False
    assert K.fits(NOW, []) is None and K.fits(None, slots) is None
