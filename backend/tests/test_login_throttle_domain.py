"""Tests for domain/login_throttle.py and domain/password_policy.py
(multi-user plan P0-03): how long a sign-in waits after failures, and which
passwords are too easy. Pure functions, no database.
Run with pytest, or directly: python3 tests/test_login_throttle_domain.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

from domain import login_throttle as LT  # noqa: E402
from domain import password_policy as PP  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def test_no_wait_below_the_email_limit():
    for n in range(LT.MAX_EMAIL_FAILS):
        assert LT.email_wait(n, NOW, NOW) == 0


def test_the_wait_doubles_from_one_minute_and_stops_at_the_cap():
    first = LT.MAX_EMAIL_FAILS
    assert [LT.email_delay(first + i) for i in range(5)] == [
        timedelta(minutes=m) for m in (1, 2, 4, 8, 15)]
    assert LT.email_delay(first + 30) == LT.MAX_DELAY


def test_the_wait_counts_from_the_last_failure():
    n = LT.MAX_EMAIL_FAILS
    assert LT.email_wait(n, NOW, NOW) == 60
    assert LT.email_wait(n, NOW, NOW + timedelta(seconds=45)) == 15
    assert LT.email_wait(n, NOW, NOW + timedelta(seconds=61)) == 0


def test_a_quiet_window_forgets_the_failures():
    late = NOW + LT.WINDOW + timedelta(seconds=1)
    assert LT.live_failures(50, NOW, late) == 0
    assert LT.email_wait(50, NOW, late) == 0 and LT.ip_wait(500, NOW, late) == 0


def test_an_address_is_closed_after_its_limit_until_the_window_passes():
    assert LT.ip_wait(LT.MAX_IP_FAILS - 1, NOW, NOW) == 0
    assert LT.ip_wait(LT.MAX_IP_FAILS, NOW, NOW) == int(LT.WINDOW.total_seconds())
    assert LT.ip_wait(LT.MAX_IP_FAILS, NOW, NOW + LT.WINDOW - timedelta(seconds=10)) == 10


@pytest.mark.parametrize("password", [
    "short", "password123", "Password123", "QWERTY12345", "1234567890", "0987654321",
    "abcdefghijk", "qwertyuiop", "1qaz2wsx3edc", "aaaaaaaaaaaa", "iloveyou12",
])
def test_easy_passwords_are_refused(password):
    assert PP.problem(password)


def test_the_emails_own_name_is_refused():
    assert PP.problem("maria-secret-2026", "maria@example.com")
    assert PP.problem("tolstoy-1828", "tolstoy@example.com")
    assert PP.problem("a long passphrase", "al@example.com") is None   # names under 4 chars


@pytest.mark.parametrize("password", ["correct horse battery", "Velvet-Tundra-41", "ferns in a teacup"])
def test_ordinary_passphrases_pass(password):
    assert PP.problem(password, "someone@example.com") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
