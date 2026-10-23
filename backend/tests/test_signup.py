"""Multi-user plan P1-06: sign-up, password reset and profile changes by
emailed link. Sign-up mails a link and the account is made only when its
password is chosen there; replies never tell whether an address is known;
links work once and expire; a reset or password change ends every other
session and API token; without SMTP sign-up and reset are off. Mail is
captured, never sent. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_signup.py
"""
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import signup as SU  # noqa: E402
from infrastructure import mailer  # noqa: E402

PW, NEW_PW = "correct horse battery", "a brand new long secret"
REAL_SEND = mailer.send


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "niche-finder <no-reply@example.test>")
    monkeypatch.setenv("NF_PUBLIC_URL", "https://niches.example.test")
    monkeypatch.delenv("NF_REGISTRATION", raising=False)
    conn = db.get_conn()
    for t in ("sessions", "api_tokens", "login_attempts", "email_tokens"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text: sent.append((to, subject, text)))
    return sent


def _client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


def _link(mail, kind):
    m = re.search(r"https://niches\.example\.test/#/(\w+)/(\S+)", mail[2])
    assert m and m.group(1) == kind, mail[2]
    return m.group(2)


def _signed_up(outbox, email="new@example.com"):
    c = _client()
    assert c.post("/api/auth/register", json={"email": email}).status_code == 200
    token = _link(outbox[-1], "signup")
    r = c.post("/api/auth/register/complete", json={"token": token, "password": PW})
    assert r.status_code == 200, r.text
    return c


def _users(email):
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,)).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------ what is offered

def test_the_sign_in_screen_learns_what_is_offered(monkeypatch):
    me = _client().get("/api/auth/me").json()
    assert me["signup"] == {"signup": True, "passwordReset": True}
    monkeypatch.setenv("NF_REGISTRATION", "off")
    assert _client().get("/api/auth/me").json()["signup"] == {"signup": False,
                                                             "passwordReset": True}
    monkeypatch.delenv("SMTP_HOST")
    assert _client().get("/api/auth/me").json()["signup"] == {"signup": False,
                                                             "passwordReset": False}


def test_without_smtp_there_is_no_sign_up_or_reset(monkeypatch, outbox):
    monkeypatch.delenv("SMTP_HOST")
    c = _client()
    assert c.post("/api/auth/register", json={"email": "a@example.com"}).status_code == 404
    assert c.post("/api/auth/password/forgot", json={"email": "a@example.com"}).status_code == 404
    assert outbox == []


# ------------------------------------------------------------ sign-up

def test_sign_up_by_link_creates_the_account_and_signs_in(outbox):
    c = _client()
    r = c.post("/api/auth/register", json={"email": " New@Example.com "})
    assert r.status_code == 200 and r.json()["sent"] is True
    assert _users("new@example.com") == 0                  # nothing exists before the link
    to, subject, _ = outbox[0]
    assert to == "new@example.com" and "niche-finder" in subject
    token = _link(outbox[0], "signup")
    assert c.post("/api/auth/register/link", json={"token": token}).json() == {
        "email": "new@example.com"}
    weak = c.post("/api/auth/register/complete", json={"token": token, "password": "short"})
    assert weak.status_code == 400                          # the link survives a weak password
    r = c.post("/api/auth/register/complete", json={"token": token, "password": PW})
    assert r.status_code == 200 and r.json()["user"]["email"] == "new@example.com"
    assert c.get("/api/auth/me").json()["user"]["email"] == "new@example.com"
    again = _client().post("/api/auth/register/complete", json={"token": token, "password": PW})
    assert again.status_code == 400                         # a link works once
    assert _client().post("/api/auth/login", json={"email": "new@example.com",
                                                   "password": PW}).status_code == 200


def test_a_known_address_gets_the_same_reply_and_a_different_email(outbox):
    AUTH.create_user("known@example.com", PW)
    r1 = _client().post("/api/auth/register", json={"email": "known@example.com"})
    r2 = _client().post("/api/auth/register", json={"email": "fresh@example.com"})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    _link(outbox[0], "reset")                               # "you already have an account"
    _link(outbox[1], "signup")
    assert _users("known@example.com") == 1


def test_a_disabled_account_gets_no_mail(outbox):
    uid = AUTH.create_user("off@example.com", PW)
    AUTH.set_disabled(uid, True)
    assert _client().post("/api/auth/register", json={"email": "off@example.com"}).status_code == 200
    assert _client().post("/api/auth/password/forgot",
                          json={"email": "off@example.com"}).status_code == 200
    assert outbox == []


def test_bad_addresses_are_refused():
    for bad in ("", "nobody", "a@b", "a b@example.com"):
        assert _client().post("/api/auth/register", json={"email": bad}).status_code == 400


def test_an_expired_link_does_not_work(outbox):
    _client().post("/api/auth/register", json={"email": "late@example.com"})
    token = _link(outbox[0], "signup")
    conn = db.get_conn()
    conn.execute("UPDATE email_tokens SET expires_at = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    conn.commit()
    conn.close()
    r = _client().post("/api/auth/register/complete", json={"token": token, "password": PW})
    assert r.status_code == 400 and _users("late@example.com") == 0


def test_links_are_stored_only_as_hashes(outbox):
    _client().post("/api/auth/register", json={"email": "hash@example.com"})
    token = _link(outbox[0], "signup")
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM email_tokens").fetchall()]
    conn.close()
    assert len(rows) == 1 and token not in repr(rows)


def test_a_failed_send_says_so(monkeypatch):
    def down(*a):
        raise mailer.MailError("SMTPConnectError")
    monkeypatch.setattr(mailer, "send", down)
    assert _client().post("/api/auth/register", json={"email": "x@example.com"}).status_code == 503


# ------------------------------------------------------------ limits

def test_one_address_gets_at_most_a_few_emails_an_hour(monkeypatch, outbox):
    monkeypatch.setattr(SU, "MAIL_PER_EMAIL", 2)
    for _ in range(4):
        assert _client().post("/api/auth/register",
                              json={"email": "spam@example.com"}).status_code == 200
    assert len(outbox) == 2                                 # quietly: the reply stays the same


def test_one_client_address_is_stopped_after_a_few_requests(monkeypatch, outbox):
    monkeypatch.setattr(SU, "MAIL_PER_IP", 2)
    codes = [_client().post("/api/auth/register", json={"email": f"u{i}@example.com"}).status_code
             for i in range(3)]
    assert codes == [200, 200, 429] and len(outbox) == 2


# ------------------------------------------------------------ forgot password

def test_reset_by_link_ends_every_session_and_token(outbox):
    uid = AUTH.create_user("me@example.com", PW)
    old = _client()
    assert old.post("/api/auth/login", json={"email": "me@example.com", "password": PW}).status_code == 200
    api_token = AUTH.create_api_token(uid)["token"]
    assert _client().post("/api/auth/password/forgot",
                          json={"email": "nobody@example.com"}).status_code == 200
    assert outbox == []                                     # unknown: same reply, no mail
    assert _client().post("/api/auth/password/forgot",
                          json={"email": "me@example.com"}).status_code == 200
    token = _link(outbox[0], "reset")
    c = _client()
    assert c.post("/api/auth/password/reset", json={"token": token, "password": NEW_PW}).status_code == 200
    assert c.get("/api/auth/me").json()["user"]["id"] == uid
    assert old.get("/api/auth/me").json()["user"] is None   # the other session ended
    assert AUTH.user_for_api_token(api_token) is None
    assert _client().post("/api/auth/login", json={"email": "me@example.com",
                                                   "password": NEW_PW}).status_code == 200
    assert _client().post("/api/auth/password/reset",
                          json={"token": token, "password": PW}).status_code == 400


# ------------------------------------------------------------ profile

def test_changing_the_password_needs_the_current_one(outbox):
    c = _signed_up(outbox)
    other = _client()
    other.post("/api/auth/login", json={"email": "new@example.com", "password": PW})
    assert c.post("/api/auth/password", json={"currentPassword": "wrong one!",
                                              "password": NEW_PW}).status_code == 400
    r = c.post("/api/auth/password", json={"currentPassword": PW, "password": NEW_PW})
    assert r.status_code == 200
    assert c.get("/api/auth/me").json()["user"]["email"] == "new@example.com"   # still in
    assert other.get("/api/auth/me").json()["user"] is None                      # signed out


def test_changing_the_email_by_link_to_the_new_address(outbox):
    c = _signed_up(outbox)
    AUTH.create_user("taken@example.com", PW)
    assert c.post("/api/auth/email", json={"password": "wrong one!",
                                           "email": "next@example.com"}).status_code == 400
    assert c.post("/api/auth/email", json={"password": PW,
                                           "email": "taken@example.com"}).status_code == 400
    r = c.post("/api/auth/email", json={"password": PW, "email": "Next@Example.com"})
    assert r.status_code == 200 and r.json() == {"sentTo": "next@example.com"}
    assert outbox[-1][0] == "next@example.com"
    token = _link(outbox[-1], "email")
    assert _client().post("/api/auth/email/confirm", json={"token": token}).json() == {
        "email": "next@example.com"}
    assert c.get("/api/auth/me").json()["user"]["email"] == "next@example.com"
    assert _client().post("/api/auth/login", json={"email": "next@example.com",
                                                   "password": PW}).status_code == 200


def test_profile_changes_need_a_dashboard_sign_in(outbox):
    _signed_up(outbox)
    uid = AUTH.user_id_for_email("new@example.com")
    bearer = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization":
                                          f"Bearer {AUTH.create_api_token(uid)['token']}"})
    assert bearer.post("/api/auth/password", json={"currentPassword": PW,
                                                   "password": NEW_PW}).status_code == 403
    assert bearer.post("/api/auth/email", json={"password": PW,
                                                "email": "x@example.com"}).status_code == 403
    assert _client().post("/api/auth/password", json={}).status_code == 401


# ------------------------------------------------------------ the mailer

class _FakeSMTP:
    sent, log = [], []

    def __init__(self, host, port, timeout=None, context=None):
        self.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.log.append(("starttls",))

    def login(self, user, password):
        self.log.append(("login", user))

    def send_message(self, msg):
        self.sent.append(msg)


def test_the_mailer_speaks_starttls_and_logs_in(monkeypatch):
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("SMTP_USER", "robot")
    monkeypatch.setenv("SMTP_PASSWORD", "pw-secret")
    _FakeSMTP.sent, _FakeSMTP.log = [], []
    REAL_SEND("to@example.com", "Subject", "Body")
    assert _FakeSMTP.log == [("connect", "smtp.example.test", 587), ("starttls",),
                             ("login", "robot")]
    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "to@example.com" and msg["From"].endswith("<no-reply@example.test>")


def test_a_failed_send_is_logged_without_the_recipient(monkeypatch, caplog):
    class Refusing(_FakeSMTP):
        def send_message(self, msg):
            raise smtplib.SMTPRecipientsRefused({})
    monkeypatch.setattr(mailer.smtplib, "SMTP", Refusing)
    with pytest.raises(mailer.MailError):
        REAL_SEND("secret-person@example.com", "S", "B")
    assert "secret-person" not in caplog.text and "SMTPRecipientsRefused" in caplog.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
