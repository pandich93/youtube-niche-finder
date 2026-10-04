"""Content calendar (plan 33): when a draft is planned, what state it is in,
when to remind about it. Pure functions, no DB. Every time is UTC; the
dashboard shows it in the browser's zone.

States: published (linked to a real video), overdue (the planned time has
passed and nothing is linked), planned (in the future), unplanned (no date).
Reminders, each sent once per planned time (moving the date makes new ones):
  soon  the release is within REMIND_BEFORE_HOURS
  due   the planned time has come and the draft is not linked yet, for up to
        DUE_GRACE_HOURS after it (no reminders for long-forgotten drafts)
"""
from datetime import datetime, timedelta

REMIND_BEFORE_HOURS = 24
DUE_GRACE_HOURS = 24
MAX_SLOTS = 3


def state(planned: datetime | None, linked: bool, now: datetime) -> str:
    if linked:
        return "published"
    if planned is None:
        return "unplanned"
    return "overdue" if planned <= now else "planned"


def reminder(planned: datetime | None, linked: bool, now: datetime) -> str | None:
    """'soon', 'due' or None for one draft at `now`."""
    if linked or planned is None:
        return None
    if now < planned <= now + timedelta(hours=REMIND_BEFORE_HOURS):
        return "soon"
    if planned <= now <= planned + timedelta(hours=DUE_GRACE_HOURS):
        return "due"
    return None


def fits(planned: datetime | None, slots) -> bool | None:
    """Whether the planned weekday and hour (UTC) is one of the best slots
    [{weekday: 'Mon', hour: 14}, ...]; None when there is nothing to compare."""
    if planned is None or not slots:
        return None
    day = planned.strftime("%a")
    return any(s["weekday"] == day and s["hour"] == planned.hour for s in slots)
