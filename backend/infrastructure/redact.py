"""Mask secrets in text bound for a log or an error message.

The safety net behind keeping secrets out of URLs in the first place
(infrastructure/youtube/client.py sends the API key in a header): anything
that still prints a traceback or an exception -- the worker log, the HTTP 500
answer -- passes its text through redact(). On 2026-10-18 the worker log
carried the YouTube API key inside an SSL error's URL.
"""
import os
import re

# key=..., access_token=..., refresh_token=..., client_secret=... in URLs/bodies
_PARAMS = re.compile(r"(?i)\b(key|api_key|access_token|refresh_token|client_secret|token)"
                     r"=([^&\s'\"<>]+)")
# Google API keys have a fixed shape
_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
# configured secrets, masked wherever they appear verbatim
# (OWN_TOKENS_KEY_OLD is a comma-separated list: each key is masked on its own)
_SECRET_ENV = ("YOUTUBE_API_KEY", "OWN_OAUTH_CLIENT_SECRET", "OWN_TOKENS_KEY",
               "OWN_TOKENS_KEY_OLD", "OPENROUTER_API_KEY", "NOTIFY_TELEGRAM_BOT_TOKEN",
               "SMTP_PASSWORD")
MASK = "<redacted>"


def redact(text) -> str:
    if text is None:
        return ""
    text = str(text)
    for name in _SECRET_ENV:
        for value in (os.environ.get(name) or "").split(","):
            value = value.strip()
            if len(value) >= 6:      # never mask short or empty values everywhere
                text = text.replace(value, MASK)
    text = _GOOGLE_KEY.sub(MASK, text)
    return _PARAMS.sub(lambda m: f"{m.group(1)}={MASK}", text)
