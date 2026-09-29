"""Generic webhook notifier -- POST {"text": ...} to any URL (Slack-compatible
incoming webhooks and most chat/automation tools accept this shape as-is).
"""
import logging

import httpx

log = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(self, url: str, timeout: float = 10.0, restricted: bool = False):
        """restricted: the address came from a user, not .env -- re-checked
        against private networks right before every send (urlguard.py)."""
        self.url = url
        self.timeout = timeout
        self.restricted = restricted

    def send(self, text: str) -> bool:
        try:
            if self.restricted:
                from infrastructure.notify.urlguard import check_public_https
                check_public_https(self.url)
            r = httpx.post(self.url, json={"text": text}, timeout=self.timeout,
                           follow_redirects=False)
            if r.status_code >= 300:
                log.warning("webhook POST failed: %s %s", r.status_code, r.text[:300])
                return False
            return True
        except Exception as e:
            log.warning("webhook POST failed: %s", e)
            return False
