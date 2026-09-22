"""Provider-agnostic contract for optional LLM enrichment.

Every caller goes through application/llm_gateway.py, never a provider
directly -- the gateway is what adds caching and the daily budget. A
provider only needs to answer complete_json() or return None; None always
means "skip enrichment", never an exception the caller has to handle.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    data: dict
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None


class LLMProvider(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, *,
                       model: str | None = None, max_tokens: int = 1024) -> LLMResult | None:
        ...
