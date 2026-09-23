"""Local Ollama provider -- POST {OLLAMA_URL}/api/chat, stream: false.

Nothing here ever leaves the machine Ollama runs on: unlike openrouter.py
there's no API key, no billing, no free-model catalog to fall back through
-- OLLAMA_MODEL names whatever model you've `ollama pull`ed yourself, and
cost_usd is always 0.0. `format` is passed the caller's JSON schema
directly (Ollama's structured-output constraint, supported since 0.5) so
the model is steered to answer in schema shape; the response is still
re-validated the same way openrouter.py validates its own responses, since
smaller local models don't always honor the constraint perfectly.
"""
import json
import logging
import re

import httpx

from infrastructure.llm.base import LLMResult

log = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(self, base_url: str, model: str | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete_json(self, system: str, user: str, schema: dict, *,
                       model: str = None, max_tokens: int = 1024) -> LLMResult | None:
        chosen = model or self.model
        if not chosen:
            log.warning("ollama: no model given (set OLLAMA_MODEL or pass model=)")
            return None

        payload = {
            "model": chosen,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        raw = self._call(payload)
        if raw is None:
            return None

        try:
            content = raw["message"]["content"]
        except (KeyError, TypeError):
            log.warning("ollama: unexpected response shape for model=%s", chosen)
            return None

        data = _extract_json(content)
        if data is None or not _validate(data, schema):
            log.warning("ollama: response failed schema validation for model=%s", chosen)
            return None

        return LLMResult(
            data=data,
            model=raw.get("model", chosen),
            prompt_tokens=raw.get("prompt_eval_count", 0),
            completion_tokens=raw.get("eval_count", 0),
            cost_usd=0.0,
        )

    def _call(self, payload: dict) -> dict | None:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as e:
            log.warning("ollama: request failed (%s) -- is `ollama serve` running at %s?",
                        e, self.base_url)
            return None
        if resp.status_code != 200:
            log.warning("ollama: HTTP %s: %s", resp.status_code, resp.text[:400])
            return None
        return resp.json()

    def available(self) -> tuple[bool, str]:
        """Used by `cli.py doctor` -- reachable, and does it have the model
        pulled. Returns (ok, message)."""
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
        except httpx.HTTPError as e:
            return False, f"недоступен по {self.base_url} ({e})"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code} на {self.base_url}/api/tags"
        names = {m.get("name") for m in resp.json().get("models", [])}
        if self.model and self.model not in names and not any(
                n.split(":")[0] == self.model for n in names if n):
            return False, f"сервер отвечает, но модели {self.model!r} нет -- `ollama pull {self.model}`"
        return True, f"ОК -- {len(names)} моделей, {self.model or '(модель не задана)'}"


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
    if not isinstance(data, dict):
        return False
    for key in schema.get("required", []):
        if key not in data:
            return False
    for key, spec in schema.get("properties", {}).items():
        if key in data and spec.get("type") and not _type_ok(data[key], spec["type"]):
            return False
    return True
