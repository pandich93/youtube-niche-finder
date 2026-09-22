"""OpenRouter provider -- POST /api/v1/chat/completions, OpenAI-compatible.

Checked against https://openrouter.ai/docs on 2026-09-23:
  * usage (including cost) is now always included in the response -- the
    documented `usage: {include: true}` request flag is deprecated and has
    no effect, so this module doesn't send it.
  * HTTP-Referer / X-OpenRouter-Title are optional attribution headers for
    OpenRouter's leaderboards; omitting them changes nothing about the call.
  * response_format: {"type": "json_schema", ...} only works on models that
    support structured output; a model that doesn't errors on the field, so
    a rejected first attempt is retried once without it, and the JSON is
    parsed out of the plain-text response instead.

FREE_MODEL_FALLBACKS was pulled from the live https://openrouter.ai/api/v1/models
catalog on 2026-09-23 (`:free` suffix, own rate limit: 50 req/day without
purchased credits, 1000/day with $10+) -- filtered down to models whose
supported_parameters include response_format/structured_outputs, then
ordered by size/capability. When no explicit `model` is passed, complete_json
walks this list until one answers instead of pinning a single free model
that might be renamed or temporarily rate-limited.
"""
import json
import logging
import re
import time

import httpx

from infrastructure.llm.base import LLMResult

URL = "https://openrouter.ai/api/v1/chat/completions"
RETRIABLE_STATUS = {429, 500, 502, 503, 524, 529}
MAX_RETRIES = 2
BACKOFF_SECONDS = (1, 2)

FREE_MODEL_FALLBACKS = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-27b:free",
    "nex-agi/nex-n2.5-pro:free",
)
DEFAULT_MODEL = FREE_MODEL_FALLBACKS[0]

log = logging.getLogger(__name__)


def _mask(text: str, key: str) -> str:
    return text.replace(key, "<KEY>") if key else text


class OpenRouterProvider:
    def __init__(self, api_key: str, referer: str = None, title: str = None,
                 timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if referer:
            self.headers["HTTP-Referer"] = referer
        if title:
            self.headers["X-OpenRouter-Title"] = title

    def complete_json(self, system: str, user: str, schema: dict, *,
                       model: str = None, max_tokens: int = 1024) -> LLMResult | None:
        # An explicit model is a deliberate, single choice -- no fallback.
        # Otherwise walk the free-model list so one rate-limited or renamed
        # model doesn't take enrichment down entirely.
        candidates = (model,) if model else FREE_MODEL_FALLBACKS
        for candidate in candidates:
            result = self._complete_for_model(candidate, system, user, schema, max_tokens)
            if result is not None:
                return result
        return None

    def _complete_for_model(self, model: str, system: str, user: str, schema: dict,
                             max_tokens: int) -> LLMResult | None:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        }
        raw = self._call(payload)
        if raw is None:
            # model may not support response_format at all -- try plain text + parse
            payload.pop("response_format", None)
            raw = self._call(payload)
        if raw is None:
            return None

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("openrouter: unexpected response shape for model=%s", model)
            return None

        data = _extract_json(content)
        if data is None or not _validate(data, schema):
            log.warning("openrouter: response failed schema validation for model=%s", model)
            return None

        usage = raw.get("usage") or {}
        return LLMResult(
            data=data,
            model=raw.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cost_usd=usage.get("cost"),
        )

    def _call(self, payload: dict) -> dict | None:
        with httpx.Client(timeout=self.timeout) as client:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    resp = client.post(URL, headers=self.headers, json=payload)
                except httpx.HTTPError as e:
                    log.warning("openrouter: request failed (%s)", _mask(str(e), self.api_key))
                    return None
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in RETRIABLE_STATUS and attempt < MAX_RETRIES:
                    time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                log.warning("openrouter: HTTP %s: %s", resp.status_code,
                            _mask(resp.text[:400], self.api_key))
                return None
        return None


def _extract_json(text) -> dict | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


_TYPE_MAP = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict,
}


def _type_ok(value, expected: str) -> bool:
    py_type = _TYPE_MAP.get(expected)
    if py_type is None:
        return True
    if expected in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py_type)


def _validate(data, schema: dict) -> bool:
    """Structural check only -- type/required/properties -- no jsonschema
    dependency for what is otherwise a single-purpose validator."""
    if not isinstance(data, dict):
        return False
    for key in schema.get("required", []):
        if key not in data:
            return False
    for key, spec in schema.get("properties", {}).items():
        if key in data and spec.get("type") and not _type_ok(data[key], spec["type"]):
            return False
    return True
