"""How long a sign-in has to wait after failed attempts (multi-user plan P0-03).

Two counters, both kept in Postgres by the caller:
- per email: after MAX_EMAIL_FAILS failures the next attempt waits 1 minute,
  then 2, 4, ... up to MAX_DELAY -- the right password included, or guessing
  would simply go on. Never a permanent lock: that would let anyone lock out
  someone else's account.
- per client address: MAX_IP_FAILS failures across any emails close sign-in
  from that address until WINDOW has passed since the last one.

A counter whose last failure is older than WINDOW starts again from zero.
Pure functions: timestamps are datetimes, the result is whole seconds to wait
(0 = may try now).
"""
import os
from datetime import datetime, timedelta


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


MAX_EMAIL_FAILS = _env_int("NF_LOGIN_MAX_FAILS", 5)
MAX_IP_FAILS = _env_int("NF_LOGIN_MAX_IP_FAILS", 30)
WINDOW = timedelta(minutes=_env_int("NF_LOGIN_WINDOW_MIN", 15))
MAX_DELAY = timedelta(minutes=15)


def live_failures(failures: int, last_failure: datetime, now: datetime) -> int:
    """Failures that still count: none once WINDOW has passed since the last."""
    if not failures or last_failure is None or now - last_failure > WINDOW:
        return 0
    return failures


def email_delay(failures: int) -> timedelta:
    """The wait after `failures` failures for one email: 0 below the limit,
    then 1, 2, 4 ... minutes, at most MAX_DELAY."""
    if failures < MAX_EMAIL_FAILS:
        return timedelta(0)
    return min(timedelta(minutes=2 ** (failures - MAX_EMAIL_FAILS)), MAX_DELAY)


def _seconds(delta: timedelta) -> int:
    s = delta.total_seconds()
    return int(s) + (1 if s > int(s) else 0) if s > 0 else 0


def email_wait(failures: int, last_failure: datetime, now: datetime) -> int:
    live = live_failures(failures, last_failure, now)
    if not live:
        return 0
    return _seconds(last_failure + email_delay(live) - now)


def ip_wait(failures: int, last_failure: datetime, now: datetime) -> int:
    live = live_failures(failures, last_failure, now)
    if live < MAX_IP_FAILS:
        return 0
    return _seconds(last_failure + WINDOW - now)
