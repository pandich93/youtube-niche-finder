"""Picks the LLMProvider from environment variables. Nothing else in the
application should import null.py/openrouter.py directly."""
import logging
import os

from infrastructure.llm.null import NullProvider
from infrastructure.llm.openrouter import FREE_MODEL_FALLBACKS, OpenRouterProvider

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


def default_model() -> str | None:
    """None means "no pinned model" -- OpenRouterProvider.complete_json()
    then walks FREE_MODEL_FALLBACKS on its own. Set OPENROUTER_MODEL to pin
    one specific (free or paid) model instead."""
    return os.environ.get("OPENROUTER_MODEL", "").strip() or None


def display_model() -> str:
    """Human-readable model description for db_stats/doctor -- default_model()
    itself stays None so the provider keeps choosing freely."""
    pinned = default_model()
    if pinned:
        return pinned
    return f"auto ({FREE_MODEL_FALLBACKS[0]} + {len(FREE_MODEL_FALLBACKS) - 1} free fallbacks)"
