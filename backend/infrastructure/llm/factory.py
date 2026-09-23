"""Picks the LLMProvider from environment variables. Nothing else in the
application should import null.py/openrouter.py directly."""
import logging
import os

from infrastructure.llm.null import NullProvider
from infrastructure.llm.ollama import OllamaProvider
from infrastructure.llm.openrouter import FREE_MODEL_FALLBACKS, OpenRouterProvider

log = logging.getLogger(__name__)

# host.docker.internal resolves to the host machine from inside a container
# (Docker Desktop on Mac/Windows, and on Linux when docker-compose.yml adds
# the extra_hosts entry) -- that's where `ollama serve` runs, since Ollama
# itself is never containerized by this project. Outside Docker (running
# cli.py directly) OLLAMA_URL should be the loopback address instead.
DEFAULT_OLLAMA_URL = "http://host.docker.internal:11434" if os.path.exists("/.dockerenv") \
    else "http://127.0.0.1:11434"


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
    if provider == "ollama":
        return OllamaProvider(
            os.environ.get("OLLAMA_URL", "").strip() or DEFAULT_OLLAMA_URL,
            model=os.environ.get("OLLAMA_MODEL", "").strip() or None,
        )
    return NullProvider()


def default_model() -> str | None:
    """None means "no pinned model" -- OpenRouterProvider.complete_json()
    then walks FREE_MODEL_FALLBACKS on its own. Set OPENROUTER_MODEL to pin
    one specific (free or paid) model instead."""
    return os.environ.get("OPENROUTER_MODEL", "").strip() or None


def long_context_model() -> str | None:
    """Stage 04: a video can have hundreds of comments, more tokens than
    some free-tier models accept -- OPENROUTER_MODEL_LONG pins a
    long-context model for that one task. Unset falls back to
    default_model() (same "let the provider choose" semantics)."""
    return os.environ.get("OPENROUTER_MODEL_LONG", "").strip() or default_model()


def display_model() -> str:
    """Human-readable model description for db_stats/doctor -- default_model()
    itself stays None so the provider keeps choosing freely."""
    if os.environ.get("LLM_PROVIDER", "none").strip().lower() == "ollama":
        return os.environ.get("OLLAMA_MODEL", "").strip() or "(OLLAMA_MODEL не задан)"
    pinned = default_model()
    if pinned:
        return pinned
    return f"auto ({FREE_MODEL_FALLBACKS[0]} + {len(FREE_MODEL_FALLBACKS) - 1} free fallbacks)"
