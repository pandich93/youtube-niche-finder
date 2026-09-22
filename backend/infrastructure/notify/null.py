"""No-op notifier -- what factory.get_notifier() returns when neither
NOTIFY_TELEGRAM_BOT_TOKEN nor NOTIFY_WEBHOOK_URL is set (the default).
Callers check isinstance(..., NullNotifier) to skip delivery entirely
(application/alerts.py:deliver), the same way worker_cycle.py skips
enrichment on infrastructure.llm.null.NullProvider -- so an unconfigured
install never even queries alert_deliveries, let alone grows it."""


class NullNotifier:
    def send(self, text: str) -> bool:
        return False
