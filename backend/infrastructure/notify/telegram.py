"""Telegram Bot API notifier -- POST to api.telegram.org/bot<token>/sendMessage.
HTML parse_mode (matches Telegram's own preferred formatting, simpler to
build than MarkdownV2's escaping rules). The bot token never appears in
logs: errors are logged with the token masked, same convention as
infrastructure/llm/openrouter.py's _mask().
"""
import logging

import httpx

log = logging.getLogger(__name__)

URL = "https://api.telegram.org/bot{token}/sendMessage"


def _mask(text: str, token: str) -> str:
    return text.replace(token, "<TOKEN>") if token else text


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> bool:
        try:
            r = httpx.post(
                URL.format(token=self.bot_token),
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                     "disable_web_page_preview": True},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                log.warning("telegram sendMessage failed: %s %s",
                           r.status_code, _mask(r.text[:300], self.bot_token))
                return False
            return True
        except Exception as e:
            log.warning("telegram sendMessage failed: %s", _mask(str(e), self.bot_token))
            return False
