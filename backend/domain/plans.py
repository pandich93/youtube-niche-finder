"""Tariffs (SaaS sub-project 1): what a plan can limit or switch on, and the
pure rules around it. The code says WHICH limits and features exist; an admin
sets their values per plan (application/plans.py stores them as JSON).

A limit is a number or None (no limit); 0 means "not at all". A key missing
from a stored plan takes the catalog default, so a key added to the code later
never locks existing plans: daily limits default to the NF_USER_DAILY_* values
from .env (0 there has always meant "no limit"), count limits to None and
features to on.
"""
import math
import os
from datetime import datetime, timezone


def _env_int(name, default):
    try:
        value = max(0, int(os.environ.get(name) or default))
    except ValueError:
        value = default
    return value or None


def _env_usd(name, default):
    try:
        value = max(0.0, float(os.environ.get(name) or default))
    except ValueError:
        value = default
    return value or None


def _none():
    return None


LIMITS = {
    "yt_units": {"label": "Квота YouTube в день", "unit": "units", "kind": "daily", "type": int,
                 "default": lambda: _env_int("NF_USER_DAILY_UNITS", 2000)},
    "search_calls": {"label": "Поисковые запросы YouTube в день", "unit": "запросов",
                     "kind": "daily", "type": int,
                     "default": lambda: _env_int("NF_USER_DAILY_SEARCH_CALLS", 20)},
    "llm_usd": {"label": "Расход на LLM в день", "unit": "$", "kind": "daily", "type": float,
                "default": lambda: _env_usd("NF_USER_DAILY_LLM_USD", 0.25)},
    "own_channels": {"label": "Свои каналы", "unit": "шт.", "kind": "count", "type": int,
                     "default": _none},
    "tracked_channels": {"label": "Каналы в отслеживании", "unit": "шт.", "kind": "count",
                         "type": int, "default": _none},
    "topics": {"label": "Темы для алертов", "unit": "шт.", "kind": "count", "type": int,
               "default": _none},
    "saved_items": {"label": "Сохранённое", "unit": "шт.", "kind": "count", "type": int,
                    "default": _none},
    "drafts": {"label": "Черновики и брифы", "unit": "шт.", "kind": "count", "type": int,
               "default": _none},
    "api_tokens": {"label": "API-токены", "unit": "шт.", "kind": "count", "type": int,
                   "default": _none},
}

FEATURES = {
    "own_channels": "Мои каналы",
    "own_revenue": "Доход своего канала",
    "llm": "Функции с LLM",
    "api_access": "MCP и браузерное расширение",
    "export": "Выгрузка ниш",
    "topic_youtube_search": "Поиск тем по всему YouTube",
    "notifications": "Алерты и дайджесты",
}


def resolve_limits(stored) -> dict:
    stored = stored if isinstance(stored, dict) else {}
    return {key: (stored[key] if key in stored else spec["default"]())
            for key, spec in LIMITS.items()}


def resolve_features(stored) -> dict:
    stored = stored if isinstance(stored, dict) else {}
    return {key: bool(stored.get(key, True)) for key in FEATURES}


def validate(limits, features) -> list:
    """Every problem with an admin's input, one string per field."""
    if not isinstance(limits, dict):
        return ["limits: ожидается объект"]
    if not isinstance(features, dict):
        return ["features: ожидается объект"]
    errors = []
    for key, value in limits.items():
        spec = LIMITS.get(key)
        if spec is None:
            errors.append(f"limits.{key}: неизвестный лимит")
        elif value is None:
            continue
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            errors.append(f"limits.{key}: нужно число или null")
        elif value < 0:
            errors.append(f"limits.{key}: не может быть меньше 0")
        elif spec["type"] is int and value != int(value):
            errors.append(f"limits.{key}: нужно целое число")
    for key, value in features.items():
        if key not in FEATURES:
            errors.append(f"features.{key}: неизвестная функция")
        elif not isinstance(value, bool):
            errors.append(f"features.{key}: нужно true или false")
    return errors


def expired(expires_at, now: datetime) -> bool:
    """No date = never expires; an unreadable date counts as expired, so a
    broken row falls back to the base plan instead of granting forever."""
    if not expires_at:
        return False
    try:
        ts = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts <= now


def has_room(limit, count: int) -> bool:
    return limit is None or count < limit


class PlanLimitReached(Exception):
    """A count limit of the user's plan is reached. Not a ValueError on
    purpose: routes that turn ValueError into 400 must let it through to the
    403 handler."""

    def __init__(self, key: str, limit: int):
        self.key, self.max = key, limit
        super().__init__(f"ваш тариф позволяет не больше {limit} ({LIMITS[key]['label'].lower()}) "
                         "-- сменить тариф можно на странице «Тариф»")


class FeatureOff(Exception):
    def __init__(self, key: str):
        self.key = key
        super().__init__(f"«{FEATURES[key]}» не входит в ваш тариф "
                         "-- сменить тариф можно на странице «Тариф»")
