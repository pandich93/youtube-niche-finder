"""Outgoing email over SMTP (multi-user plan P1-06): the sign-up link, the
password reset link and the new-email confirmation. Configured only from
.env, sent with the standard library's smtplib -- no provider SDK.

    SMTP_HOST, SMTP_FROM        required; without them nothing is sent and
                                configured() is False (sign-up and reset are off)
    SMTP_PORT                   default 587 (starttls), 465 (ssl), 25 (none)
    SMTP_SECURITY               starttls (default) | ssl | none
    SMTP_USER, SMTP_PASSWORD    when the server wants a login

Logs never carry the recipient or the message: only that a send failed and
the error's type.
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("niche-finder.mailer")

TIMEOUT_SECONDS = 15
_PORTS = {"starttls": 587, "ssl": 465, "none": 25}


class MailError(RuntimeError):
    """The message could not be handed to the SMTP server."""


def _security() -> str:
    mode = (os.environ.get("SMTP_SECURITY") or "starttls").strip().lower()
    return mode if mode in _PORTS else "starttls"


def configured() -> bool:
    return bool((os.environ.get("SMTP_HOST") or "").strip()
                and (os.environ.get("SMTP_FROM") or "").strip())


def _port(mode: str) -> int:
    try:
        return int(os.environ.get("SMTP_PORT") or _PORTS[mode])
    except ValueError:
        return _PORTS[mode]


def send(to: str, subject: str, text: str) -> None:
    """Send one plain-text message; MailError when it does not go out."""
    if not configured():
        raise MailError("SMTP is not configured (SMTP_HOST, SMTP_FROM)")
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_FROM"].strip()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    host, mode = os.environ["SMTP_HOST"].strip(), _security()
    user, password = os.environ.get("SMTP_USER") or "", os.environ.get("SMTP_PASSWORD") or ""
    try:
        if mode == "ssl":
            server = smtplib.SMTP_SSL(host, _port(mode), timeout=TIMEOUT_SECONDS,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, _port(mode), timeout=TIMEOUT_SECONDS)
        with server:
            if mode == "starttls":
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.warning("email not sent: %s", type(e).__name__)
        raise MailError(type(e).__name__) from None
