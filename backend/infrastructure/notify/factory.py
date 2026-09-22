"""Picks the Notifier from environment variables -- Telegram takes priority
over a generic webhook if both are set. Nothing else in the application
should import telegram.py/webhook.py directly."""
import os

from infrastructure.notify.null import NullNotifier
from infrastructure.notify.telegram import TelegramNotifier
from infrastructure.notify.webhook import WebhookNotifier


def get_notifier():
    token = os.environ.get("NOTIFY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("NOTIFY_TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        return TelegramNotifier(token, chat_id)

    webhook_url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    if webhook_url:
        return WebhookNotifier(webhook_url)

    return NullNotifier()


def display_target() -> str:
    """Human-readable notify target for db_stats/doctor."""
    if isinstance(get_notifier(), TelegramNotifier):
        return "telegram"
    if isinstance(get_notifier(), WebhookNotifier):
        return "webhook"
    return "none"
