"""Generic webhook notifier -- POST {"text": ...} to any URL (Slack-compatible
incoming webhooks and most chat/automation tools accept this shape as-is).
"""
import logging

import httpx

log = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout

    def send(self, text: str) -> bool:
        try:
            r = httpx.post(self.url, json={"text": text}, timeout=self.timeout)
            if r.status_code >= 300:
                log.warning("webhook POST failed: %s %s", r.status_code, r.text[:300])
                return False
            return True
        except Exception as e:
            log.warning("webhook POST failed: %s", e)
            return False
