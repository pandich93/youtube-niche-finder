"""Interface language (i18n plan): texts a person reads -- hints, catalog
labels, API error details, alerts and the digest -- in Russian or English.

gettext style: the source string IS the Russian text already in the code, so
wrapping it changes nothing for Russian, and an English catalog maps it to the
translation (domain/locales/en/*.py, each module a `MESSAGES = {ru: en}` dict;
every module of the package is merged on first use, so areas never collide).
A string without a translation comes back as is.

    from domain.i18n import tr
    tr("нужно поле channel")
    tr("Фильтр '{name}' отсёк все {before} видео", name=name, before=n)

(tr, not gettext's `_`: the code uses `_` as a throwaway name in a couple of
dozen places, and importing `_` would shadow it.)

Params are substituted AFTER the lookup, so the catalog key keeps its
{placeholders}. Translate where the text leaves for the reader, never at import
time: module-level constants stay Russian and are wrapped where they are used.

The language of the current request or delivery is a ContextVar (like
infrastructure/quota_owner.py): the HTTP layer sets it from X-NF-Locale /
Accept-Language, the worker per recipient. Nothing set -> NF_DEFAULT_LOCALE,
"ru" unless the installation says otherwise, so API clients that send no
header see exactly what they saw before.

MCP scenarios follow the user's saved language (interfaces/mcp/auth.py
locale_middleware); their titles and the CLI use NF_DEFAULT_LOCALE.

Not for: detection patterns (hook/policy/sponsor regexes match real videos),
MCP tool descriptions (English, Claude reads them) and logs.
"""
import importlib
import os
import pkgutil
from contextlib import contextmanager
from contextvars import ContextVar

SUPPORTED = ("ru", "en")
SOURCE_LOCALE = "ru"
# languages whose speakers get the Russian interface when they ask for none of ours
_RU_FAMILY = {"ru", "uk", "be", "kk", "uz", "ky", "tg"}

_locale = ContextVar("nf_locale", default=None)
_catalogs = {}


def default_locale() -> str:
    value = (os.environ.get("NF_DEFAULT_LOCALE") or SOURCE_LOCALE).strip().lower()
    return value if value in SUPPORTED else SOURCE_LOCALE


def normalize(value):
    """'en-US' -> 'en', 'uk' -> 'ru', anything we do not serve -> None."""
    base = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    if base in SUPPORTED:
        return base
    return "ru" if base in _RU_FAMILY else None


def from_accept_language(header):
    """The first language of an Accept-Language header we can serve, by q."""
    ranked = []
    for i, part in enumerate((header or "").split(",")):
        lang, _sep, params = part.strip().partition(";")
        q = 1.0
        for p in params.split(";"):
            k, _eq, v = p.strip().partition("=")
            if k == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 0.0
        if lang and lang != "*" and q > 0:
            ranked.append((-q, i, lang))
    for _q, _i, lang in sorted(ranked):
        loc = normalize(lang)
        if loc:
            return loc
    return None


def current_locale() -> str:
    return _locale.get() or default_locale()


def set_locale(locale):
    """Token for reset(); an unsupported value means 'the default'."""
    return _locale.set(normalize(locale))


def reset(token):
    _locale.reset(token)


@contextmanager
def use_locale(locale):
    token = set_locale(locale)
    try:
        yield
    finally:
        reset(token)


def _catalog(locale: str) -> dict:
    if locale not in _catalogs:
        merged = {}
        package = importlib.import_module(f"domain.locales.{locale}")
        for mod in pkgutil.iter_modules(package.__path__):
            merged.update(getattr(importlib.import_module(f"{package.__name__}.{mod.name}"),
                                  "MESSAGES", {}))
        _catalogs[locale] = merged
    return _catalogs[locale]


def translate(source: str, locale: str = None) -> str:
    locale = locale or current_locale()
    if locale == SOURCE_LOCALE or not source:
        return source
    return _catalog(locale).get(source, source)


def tr(source: str, **params) -> str:
    text = translate(source)
    return text.format(**params) if params else text
