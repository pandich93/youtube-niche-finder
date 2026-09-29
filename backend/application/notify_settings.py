"""Each user's own alert delivery (plan 15, sub-stage 5.9): a Telegram bot and
chat, or a webhook, plus the mode -- instant, digest, both or off.

User 1 without a saved row keeps the NOTIFY_* settings from .env, exactly as
before, so single-user installs change nothing. The bot token and the webhook
address are Fernet-encrypted (infrastructure/secrets.py, OWN_TOKENS_KEY) and
never returned: reading the settings says only whether each is set. A webhook
address from a user must be https to a public address (urlguard.py).
"""
import infrastructure.postgres as db
import infrastructure.secrets as SEC
from domain.users import LOCAL_USER_ID
from infrastructure.notify.null import NullNotifier
from infrastructure.notify.telegram import TelegramNotifier
from infrastructure.notify.urlguard import UnsafeUrl, check_public_https
from infrastructure.notify.webhook import WebhookNotifier

MODES = ("instant", "digest", "both", "off")


class SettingsError(ValueError):
    pass


def _row(conn, user_id):
    return conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()


def _env_mode():
    from application import alerts
    return alerts.NOTIFY_MODE


def get(user_id: int = LOCAL_USER_ID) -> dict:
    conn = db.get_conn()
    try:
        r = _row(conn, user_id)
    finally:
        conn.close()
    if r is None:
        if user_id == LOCAL_USER_ID:
            from infrastructure.notify import factory
            return {"source": "env", "telegram": factory.display_target() == "telegram",
                    "telegramChatId": None, "webhook": factory.display_target() == "webhook",
                    "mode": _env_mode()}
        return {"source": "none", "telegram": False, "telegramChatId": None, "webhook": False,
                "mode": "instant"}
    return {"source": "settings", "telegram": bool(r["telegram_token_enc"] and r["telegram_chat_id"]),
            "telegramChatId": r["telegram_chat_id"], "webhook": bool(r["webhook_url_enc"]),
            "mode": r["notify_mode"] or "instant", "updatedAt": r["updated_at"]}


def save(user_id: int, telegram_bot_token: str = None, telegram_chat_id: str = None,
         webhook_url: str = None, mode: str = None, clear: bool = False) -> dict:
    """None keeps a field as it is; "" clears it; clear=True drops the row
    (user 1 then falls back to .env)."""
    conn = db.get_conn()
    try:
        if clear:
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            conn.commit()
            return get(user_id)
        r = _row(conn, user_id)
        cur = dict(r) if r else {"telegram_token_enc": None, "telegram_chat_id": None,
                                 "webhook_url_enc": None, "notify_mode": None}
        if mode is not None:
            if mode not in MODES:
                raise SettingsError(f"mode must be one of {', '.join(MODES)}")
            cur["notify_mode"] = mode
        if telegram_chat_id is not None:
            cur["telegram_chat_id"] = telegram_chat_id.strip() or None
        try:
            if telegram_bot_token is not None:
                cur["telegram_token_enc"] = (SEC.encrypt(telegram_bot_token.strip())
                                             if telegram_bot_token.strip() else None)
            if webhook_url is not None:
                if webhook_url.strip():
                    cur["webhook_url_enc"] = SEC.encrypt(check_public_https(webhook_url))
                else:
                    cur["webhook_url_enc"] = None
        except SEC.SecretsNotConfigured:
            raise SettingsError("set OWN_TOKENS_KEY in .env: notification secrets are stored "
                                "encrypted") from None
        except UnsafeUrl as e:
            raise SettingsError(str(e)) from None
        conn.execute(
            "INSERT INTO user_settings (user_id, telegram_token_enc, telegram_chat_id, webhook_url_enc, "
            "notify_mode, updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT (user_id) DO UPDATE SET "
            "telegram_token_enc = EXCLUDED.telegram_token_enc, telegram_chat_id = EXCLUDED.telegram_chat_id, "
            "webhook_url_enc = EXCLUDED.webhook_url_enc, notify_mode = EXCLUDED.notify_mode, "
            "updated_at = EXCLUDED.updated_at",
            (user_id, cur["telegram_token_enc"], cur["telegram_chat_id"], cur["webhook_url_enc"],
             cur["notify_mode"], db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    return get(user_id)


def notifier_for(user_id: int = LOCAL_USER_ID):
    conn = db.get_conn()
    try:
        r = _row(conn, user_id)
    finally:
        conn.close()
    if r is None:
        if user_id == LOCAL_USER_ID:
            from infrastructure.notify import factory
            return factory.get_notifier()
        return NullNotifier()
    try:
        if r["telegram_token_enc"] and r["telegram_chat_id"]:
            return TelegramNotifier(SEC.decrypt(r["telegram_token_enc"]), r["telegram_chat_id"])
        if r["webhook_url_enc"]:
            return WebhookNotifier(SEC.decrypt(r["webhook_url_enc"]), restricted=True)
    except SEC.SecretsNotConfigured:
        return NullNotifier()
    return NullNotifier()


def mode_for(user_id: int = LOCAL_USER_ID) -> str:
    conn = db.get_conn()
    try:
        r = _row(conn, user_id)
    finally:
        conn.close()
    if r is None or not r["notify_mode"]:
        return _env_mode() if user_id == LOCAL_USER_ID else "instant"
    return r["notify_mode"]


def recipients() -> list:
    """Users who may get alerts: the local user (.env or saved settings) and
    everyone who saved settings."""
    conn = db.get_conn()
    try:
        ids = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM user_settings ORDER BY user_id").fetchall()]
    finally:
        conn.close()
    return sorted({LOCAL_USER_ID, *ids})


def target_name(user_id: int = LOCAL_USER_ID) -> str:
    n = notifier_for(user_id)
    return ("telegram" if isinstance(n, TelegramNotifier) else
            "webhook" if isinstance(n, WebhookNotifier) else "none")

