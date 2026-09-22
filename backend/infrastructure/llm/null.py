"""The default provider -- LLM_PROVIDER=none (or unset). Every call is a
no-op so the rest of the application behaves exactly as it did before this
module existed."""
from infrastructure.llm.base import LLMResult


class NullProvider:
    def complete_json(self, system: str, user: str, schema: dict, *,
                       model: str | None = None, max_tokens: int = 1024) -> LLMResult | None:
        return None
