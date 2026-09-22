"""Picks the LLMProvider from environment variables. Nothing else in the
application should import null.py/openrouter.py directly."""
import logging
import os

from infrastructure.llm.null import NullProvider
from infrastructure.llm.openrouter import DEFAULT_MODEL, OpenRouterProvider

log = logging.getLogger(__name__)


def get_provider():
    provider = os.environ.get("LLM_PROVIDER", "none").strip().lower()
    if provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            log.warning("LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set -- "
                        "falling back to NullProvider")
            return NullProvider()
        return OpenRouterProvider(
            key,
            referer=os.environ.get("OPENROUTER_REFERER") or None,
            title=os.environ.get("OPENROUTER_TITLE") or None,
        )
    return NullProvider()


def default_model() -> str:
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
