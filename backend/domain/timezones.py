"""Часовой пояс пользователя (P2-03): имена IANA («Europe/Moscow»), проверка
через zoneinfo и перевод момента в местное время. Чистые функции, без БД --
сохранённый пояс читает application/timezone_prefs.py.

Пусто (None) везде значит «как раньше»: время контейнера (TZ в
docker-compose.yml) для часа дайджеста и UTC в тексте напоминаний.

Пояс текущей доставки -- ContextVar, как язык в domain/i18n.py: worker ставит
пояс получателя на время сборки его сообщений (alerts.deliver,
digest.send_digest), и тексты вроде напоминания календаря пишут время по нему.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from domain.periods import parse_utc

MAX_LEN = 64
# только «Область/Город» из букв, цифр, _ + -: ни «..», ни абсолютных путей --
# zoneinfo ищет имя файлом в TZPATH
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")

# Браузеры (ICU/CLDR) до сих пор отдают старые имена, а в образе Debian ссылок
# backward нет (tzdata-legacy): храним каноническое имя.
LEGACY = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
    "Europe/Kiev": "Europe/Kyiv",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "America/Godthab": "America/Nuuk",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca": "America/Argentina/Catamarca",
    "America/Cordoba": "America/Argentina/Cordoba",
    "America/Jujuy": "America/Argentina/Jujuy",
    "America/Mendoza": "America/Argentina/Mendoza",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Louisville": "America/Kentucky/Louisville",
    "Africa/Asmera": "Africa/Asmara",
    "Pacific/Truk": "Pacific/Chuuk",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Pacific/Enderbury": "Pacific/Kanton",
}

_current = ContextVar("nf_timezone", default=None)


class TimezoneError(ValueError):
    pass


def normalize(name):
    """Имя пояса IANA в каноническом виде; пусто -> None (пояс не задан).
    Неизвестное имя -- TimezoneError."""
    value = str(name or "").strip()
    if not value:
        return None
    value = LEGACY.get(value, value)
    if len(value) > MAX_LEN or not _NAME.match(value):
        raise TimezoneError(f"unknown time zone {value[:MAX_LEN]!r}: use an IANA name "
                            "such as Europe/Moscow")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise TimezoneError(f"unknown time zone {value!r}: use an IANA name such as Europe/Moscow")
    return value


def to_local(moment: datetime, name) -> datetime:
    """Момент во времени пояса name; None -- как до P2-03: момент со смещением
    остаётся в своём (worker передаёт время сервера), наивный считается
    временем сервера, как у datetime.astimezone()."""
    if name:
        return moment.astimezone(ZoneInfo(name))
    return moment if moment.tzinfo else moment.astimezone()


def local_day(moment: datetime, name) -> tuple:
    """(дата ISO, час) момента по местному времени -- «сутки» дайджеста. Дата,
    а не час, держит «один раз в сутки»: при переводе часов назад час
    повторяется, а дата -- нет; пропущенный при переводе вперёд час не
    пропускает отправку, потому что проверяется «не раньше часа»."""
    local = to_local(moment, name)
    return local.date().isoformat(), local.hour


def sent_today(mark, moment: datetime, name) -> bool:
    """Была ли последняя отправка (mark) в местные сутки момента moment или
    позже. mark -- момент отправки в UTC (ISO); местную дату обоих считаем в
    ТЕКУЩЕМ поясе, так что смена пояса не даёт второго дайджеста в те же
    сутки и не съедает следующие. Старая метка (до P2-03) -- просто дата по
    времени сервера: сравнивается как дата."""
    if not mark:
        return False
    local = to_local(moment, name)
    today = local.date()
    if len(mark) == 10:
        return mark >= today.isoformat()
    last = parse_utc(mark)
    # без пояса метку (UTC) переводим в смещение самого moment, а не оставляем в UTC
    return last is not None and last.astimezone(local.tzinfo).date() >= today


@contextmanager
def use_timezone(name):
    """Пояс получателя на время сборки его сообщений."""
    token = _current.set(name or None)
    try:
        yield
    finally:
        _current.reset(token)


def current():
    return _current.get()


def format_utc(iso: str) -> str:
    """«2026-10-25 15:00 Europe/Moscow» для момента ISO (UTC) в поясе текущей
    доставки; пояс не задан -- «… UTC», как писалось до P2-03."""
    moment = parse_utc(iso) if iso else None
    if moment is None:
        return f"{(iso or '')[:16].replace('T', ' ')} UTC".strip()
    name = current()
    local = moment.astimezone(ZoneInfo(name)) if name else moment.astimezone(timezone.utc)
    return f"{local:%Y-%m-%d %H:%M} {name or 'UTC'}"
