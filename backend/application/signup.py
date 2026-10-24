"""Self-service accounts (multi-user plan P1-06) on top of application/auth.py:
sign-up, "forgot password", and changing one's own password and email.

Sign-up takes two steps, so that only whoever reads the mailbox picks the
password: register(email) mails a one-time link, and complete_signup(token,
password) creates the account and signs in. Nothing exists before the link
is used, so nobody can claim someone else's address with a password of their
own. Every emailed link is a random token whose SHA-256 alone is stored
(email_tokens); it works once and expires.

The answers to register() and forgot_password() never tell whether an
address has an account: a known address gets a different email, not a
different reply. Mail is limited per client address (NF_MAIL_PER_IP_HOUR)
and per recipient (NF_MAIL_PER_EMAIL_HOUR), counted in login_attempts like
the sign-in throttle of P0-03. Without SMTP (infrastructure/mailer) sign-up
and reset are off; NF_REGISTRATION=off closes sign-up alone.
"""
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
from application import audit as AUD
from application import auth as AUTH
from application import two_factor as TF
from domain.i18n import tr
from domain.users import multi_user_enabled
from infrastructure import mailer

SIGNUP_HOURS = 24
RESET_HOURS = 1
EMAIL_CHANGE_HOURS = 24
MAIL_WINDOW = timedelta(hours=1)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


MAIL_PER_IP = _env_int("NF_MAIL_PER_IP_HOUR", 10)
MAIL_PER_EMAIL = _env_int("NF_MAIL_PER_EMAIL_HOUR", 3)


class SignupError(ValueError):
    """Bad input; the HTTP layer answers 400."""


class SignupClosed(SignupError):
    """Sign-up or reset is not offered on this server (404)."""


class TooManyRequests(SignupError):
    """Too many emails asked for from this address or to this recipient (429)."""


class LinkInvalid(SignupError):
    """An emailed link that is unknown, used or expired (400)."""


def registration_open() -> bool:
    return (multi_user_enabled() and mailer.configured()
            and (os.environ.get("NF_REGISTRATION") or "open").strip().lower() == "open")


def reset_available() -> bool:
    return multi_user_enabled() and mailer.configured()


def options() -> dict:
    """What the sign-in screen may offer."""
    return {"signup": registration_open(), "passwordReset": reset_available()}


def _now():
    return datetime.now(timezone.utc)


def _check_email(email) -> str:
    email = AUTH._norm(email)
    if len(email) > 254 or not _EMAIL.match(email):
        raise SignupError(tr("Нужен правильный адрес электронной почты."))
    return email


def _too_many(conn, kind: str, key: str, limit: int, now) -> bool:
    """Count one more email asked for under (kind, key); True past the limit."""
    if not key:
        return False
    n = db.record_login_failure(conn, kind, key, now.isoformat(), (now - MAIL_WINDOW).isoformat())
    return n > limit


def _refuse_ip(conn, client_ip, now):
    if _too_many(conn, "mail-ip", client_ip, MAIL_PER_IP, now):
        conn.commit()
        raise TooManyRequests(tr("Слишком много запросов писем с вашего адреса. "
                                 "Попробуйте через час."))


def _issue(conn, purpose: str, hours: int, now, *, user_id, new_email=None) -> str:
    """A new one-time link; it replaces an earlier one of the same kind."""
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM email_tokens WHERE expires_at < ?", (now.isoformat(),))
    if user_id is not None:
        conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?",
                     (user_id, purpose))
    else:
        conn.execute("DELETE FROM email_tokens WHERE new_email = ? AND purpose = ?",
                     (new_email, purpose))
    conn.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, new_email, created_at, "
                 "expires_at) VALUES (?,?,?,?,?,?)",
                 (AUTH._token_hash(token), user_id, purpose, new_email, now.isoformat(),
                  (now + timedelta(hours=hours)).isoformat()))
    return token


def _invalid():
    return LinkInvalid(tr("Ссылка недействительна или устарела. Запросите новую."))


def _peek(conn, token: str, purpose: str):
    row = conn.execute("SELECT user_id, new_email, expires_at FROM email_tokens "
                       "WHERE token_hash = ? AND purpose = ?",
                       (AUTH._token_hash(token or ""), purpose)).fetchone()
    if not row or AUTH._expired(row["expires_at"]):
        raise _invalid()
    return row


def _use(conn, token: str, purpose: str):
    """Spend the link; a second use (or a race) finds nothing."""
    if not conn.execute("DELETE FROM email_tokens WHERE token_hash = ? AND purpose = ? "
                        "RETURNING user_id", (AUTH._token_hash(token or ""), purpose)).fetchone():
        raise _invalid()


# ---------------------------------------------------------------- sign-up

def register(email: str, *, client_ip=None, link) -> None:
    """Mail a sign-up link to a new address, or a "you already have an
    account" note with a reset link to a known one -- the reply is the same.
    link(kind, token) builds the dashboard URL the email points to."""
    if not registration_open():
        raise SignupClosed(tr("Регистрация на этом сервере выключена."))
    email = _check_email(email)
    now = _now()
    conn = db.get_conn()
    try:
        _refuse_ip(conn, client_ip, now)
        if _too_many(conn, "mail-email", email, MAIL_PER_EMAIL, now):
            conn.commit()                  # quietly: the reply must not tell this address apart
            return
        user = conn.execute("SELECT id, disabled_at FROM users WHERE email = ?",
                            (email,)).fetchone()
        if user and user["disabled_at"]:
            conn.commit()
            return
        if user:
            token = _issue(conn, "reset", RESET_HOURS, now, user_id=user["id"])
            subject = tr("У вас уже есть аккаунт в niche-finder")
            text = tr("Здравствуйте!\n\nКто-то (возможно, вы) хотел зарегистрироваться в "
                      "niche-finder с этим адресом, но аккаунт с ним уже есть. Войдите со своим "
                      "паролем или задайте новый по ссылке:\n{link}\n\nСсылка действует {hours} ч "
                      "и сработает один раз. Если это были не вы, ничего делать не нужно.",
                      link=link("reset", token), hours=RESET_HOURS)
        else:
            token = _issue(conn, "signup", SIGNUP_HOURS, now, user_id=None, new_email=email)
            subject = tr("Подтвердите регистрацию в niche-finder")
            text = tr("Здравствуйте!\n\nЧтобы закончить регистрацию в niche-finder, откройте "
                      "ссылку и задайте пароль:\n{link}\n\nСсылка действует {hours} ч и сработает "
                      "один раз. Если вы не регистрировались, просто удалите это письмо.",
                      link=link("signup", token), hours=SIGNUP_HOURS)
        conn.commit()
    finally:
        conn.close()
    mailer.send(email, subject, text)


def signup_email(token: str) -> str:
    """The address a sign-up link is for, to show it on the password form."""
    conn = db.get_conn()
    try:
        return _peek(conn, token, "signup")["new_email"]
    finally:
        conn.close()


def complete_signup(token: str, password: str) -> dict:
    """Create the account the link was mailed for, with this password, and
    sign in: a session like AUTH.login returns."""
    if not registration_open():
        raise SignupClosed(tr("Регистрация на этом сервере выключена."))
    conn = db.get_conn()
    try:
        email = _peek(conn, token, "signup")["new_email"]
        AUTH._check_password(password, email)
        _use(conn, token, "signup")
        row = conn.execute("INSERT INTO users (email, password_hash, is_admin, created_at) "
                           "VALUES (?,?,0,?) ON CONFLICT (email) DO NOTHING RETURNING id",
                           (email, AUTH.hash_password(password), db.now_iso())).fetchone()
        if not row:
            conn.commit()
            raise SignupError(tr("Аккаунт с этим адресом уже есть: войдите или сбросьте пароль."))
        session = AUTH.start_session(conn, row["id"])
        AUD.record(conn, "signup", actor_id=row["id"], target_id=row["id"], target_email=email)
        conn.commit()
        return session
    finally:
        conn.close()


# ---------------------------------------------------------- forgot password

def forgot_password(email: str, *, client_ip=None, link) -> None:
    """Mail a reset link if the address has an active account; the reply is
    the same either way."""
    if not reset_available():
        raise SignupClosed(tr("Сброс пароля на этом сервере не настроен: обратитесь к "
                              "администратору."))
    email = _check_email(email)
    now = _now()
    conn = db.get_conn()
    try:
        _refuse_ip(conn, client_ip, now)
        if _too_many(conn, "mail-email", email, MAIL_PER_EMAIL, now):
            conn.commit()
            return
        user = conn.execute("SELECT id FROM users WHERE email = ? AND disabled_at IS NULL",
                            (email,)).fetchone()
        token = _issue(conn, "reset", RESET_HOURS, now, user_id=user["id"]) if user else None
        conn.commit()
    finally:
        conn.close()
    if token:
        mailer.send(email, tr("Сброс пароля в niche-finder"), tr(
            "Здравствуйте!\n\nЧтобы задать новый пароль в niche-finder, откройте ссылку:\n{link}\n\n"
            "Ссылка действует {hours} ч и сработает один раз. После смены пароля все входы и "
            "API-токены этого аккаунта завершатся. Если вы не просили сброс, просто удалите это "
            "письмо.", link=link("reset", token), hours=RESET_HOURS))


def reset_password(token: str, password: str) -> dict:
    """Set the new password from a reset link -- every session and API token
    of the account ends, as with any password change -- and sign in here; with
    two-factor sign-in on (P2-04), the second step's challenge instead."""
    conn = db.get_conn()
    try:
        uid = _peek(conn, token, "reset")["user_id"]
        user = conn.execute("SELECT email FROM users WHERE id = ? AND disabled_at IS NULL",
                            (uid,)).fetchone()
        if not user:
            raise _invalid()
        AUTH._check_password(password, user["email"])
        _use(conn, token, "reset")
        conn.commit()
        AUTH.set_password(user["email"], password)
        if TF.enabled(uid, conn=conn):
            # P2-04: письмо -- не второй фактор; 2FA остаётся, вход -- вторым шагом
            AUD.record(conn, "password_reset", actor_id=uid, target_id=uid,
                       details={"secondFactor": "required"})
            challenge = TF.start_challenge(conn, uid)
            conn.commit()
            return challenge
        session = AUTH.start_session(conn, uid)
        AUD.record(conn, "password_reset", actor_id=uid, target_id=uid)
        conn.commit()
        return session
    finally:
        conn.close()


# --------------------------------------------------------------- profile

def _user_with_password(conn, user_id: int, password: str):
    row = conn.execute("SELECT email, password_hash FROM users WHERE id = ?",
                       (user_id,)).fetchone()
    if not row or not AUTH.verify_password(password or "", row["password_hash"]):
        raise SignupError(tr("Неверный пароль."))
    return row


def change_password(user_id: int, current: str, new: str) -> dict:
    """With the current password: every other sign-in and every API token
    ends; this browser gets a new session."""
    conn = db.get_conn()
    try:
        email = _user_with_password(conn, user_id, current)["email"]
    finally:
        conn.close()
    AUTH.set_password(email, new)
    conn = db.get_conn()
    try:
        session = AUTH.start_session(conn, user_id)
        AUD.record(conn, "password_change", actor_id=user_id, target_id=user_id)
        conn.commit()
        return session
    finally:
        conn.close()


def request_email_change(user_id: int, password: str, new_email: str, *, client_ip=None,
                         link) -> str:
    """Mail a confirmation link to the new address; the email changes when it
    is opened. Returns the address the link went to."""
    if not mailer.configured():
        raise SignupClosed(tr("Сменить email можно, только когда на сервере настроена почта "
                              "(SMTP)."))
    new = _check_email(new_email)
    now = _now()
    conn = db.get_conn()
    try:
        user = _user_with_password(conn, user_id, password)
        if new == user["email"]:
            raise SignupError(tr("Это уже ваш адрес."))
        _refuse_ip(conn, client_ip, now)
        if _too_many(conn, "mail-email", new, MAIL_PER_EMAIL, now):
            conn.commit()
            raise TooManyRequests(tr("На этот адрес уже отправлено много писем. Попробуйте "
                                     "через час."))
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (new,)).fetchone():
            conn.commit()
            raise SignupError(tr("Этот адрес уже занят другим аккаунтом."))
        token = _issue(conn, "email", EMAIL_CHANGE_HOURS, now, user_id=user_id, new_email=new)
        AUD.record(conn, "email_change_requested", actor_id=user_id, target_id=user_id,
                   details={"from": user["email"], "to": new})
        conn.commit()
    finally:
        conn.close()
    mailer.send(new, tr("Подтвердите новый email в niche-finder"), tr(
        "Здравствуйте!\n\nЧтобы этот адрес стал адресом вашего аккаунта в niche-finder, "
        "откройте ссылку:\n{link}\n\nСсылка действует {hours} ч и сработает один раз. Если вы "
        "ничего не меняли, просто удалите это письмо.",
        link=link("email", token), hours=EMAIL_CHANGE_HOURS))
    return new


def confirm_email_change(token: str) -> str:
    """Make the confirmed address the account's email; returns it."""
    conn = db.get_conn()
    try:
        row = _peek(conn, token, "email")
        _use(conn, token, "email")
        old = conn.execute("SELECT email FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        done = conn.execute("UPDATE users SET email = ? WHERE id = ? AND NOT EXISTS "
                            "(SELECT 1 FROM users WHERE email = ?) RETURNING id",
                            (row["new_email"], row["user_id"], row["new_email"])).fetchone()
        if done:
            AUD.record(conn, "email_change", actor_id=row["user_id"], target_id=row["user_id"],
                       target_email=row["new_email"],
                       details={"from": old["email"] if old else None, "to": row["new_email"]})
        conn.commit()
        if not done:
            raise SignupError(tr("Этот адрес уже занят другим аккаунтом."))
        return row["new_email"]
    finally:
        conn.close()
