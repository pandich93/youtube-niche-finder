"""Часовой пояс пользователя (P2-03): users.timezone, имя IANA. По нему
worker отправляет дайджест в DIGEST_HOUR местного времени и пишет время в
напоминаниях календаря. Однопользовательский режим хранит пояс у локального
пользователя (id 1), как и всё остальное.

Не задан -> NF_DEFAULT_TIMEZONE из .env, а нет и его -> время сервера
(TZ контейнера), то есть ровно как до P2-03. Суточные квоты от пояса не
зависят: YouTube считает сутки по Тихоокеанскому времени, LLM -- по UTC.
"""
import logging
import os

import infrastructure.postgres as db
from domain import timezones as TZ

_log = logging.getLogger(__name__)
TimezoneError = TZ.TimezoneError


def default_name():
    """NF_DEFAULT_TIMEZONE, если это настоящий пояс; иначе None (время
    сервера). Опечатка в .env не роняет worker -- только предупреждение."""
    raw = os.environ.get("NF_DEFAULT_TIMEZONE", "")
    try:
        return TZ.normalize(raw)
    except TZ.TimezoneError:
        _log.warning("NF_DEFAULT_TIMEZONE=%r is not an IANA time zone; using the server time", raw)
        return None


def server_name():
    """Как подписать «по умолчанию» в профиле: NF_DEFAULT_TIMEZONE, иначе TZ
    контейнера, иначе None (неизвестно)."""
    name = default_name()
    if name:
        return name
    try:
        return TZ.normalize(os.environ.get("TZ", ""))
    except TZ.TimezoneError:
        return None


def get(*, user_id: int):
    """Сохранённый пояс или None, если человек его не выбирал."""
    conn = db.get_conn()
    try:
        r = conn.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return (r["timezone"] or None) if r else None


def effective(*, user_id: int):
    """Пояс, по которому живут сутки этого пользователя: свой, иначе
    NF_DEFAULT_TIMEZONE; None -- время сервера. Ошибка чтения не мешает
    доставке: тогда как у всех по умолчанию."""
    try:
        saved = get(user_id=user_id)
    except Exception:
        saved = None
    return saved or default_name()


def save(name, *, user_id: int, only_if_empty: bool = False):
    """Сохранить пояс (пусто -- сбросить на значение по умолчанию). only_if_empty:
    молчаливое «как в браузере» при первом входе -- не трогает уже выбранный.
    Возвращает то, что теперь сохранено."""
    value = TZ.normalize(name)
    conn = db.get_conn()
    try:
        if only_if_empty:
            conn.execute("UPDATE users SET timezone = ? WHERE id = ? "
                         "AND (timezone IS NULL OR timezone = '')", (value, user_id))
        else:
            conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (value, user_id))
        r = conn.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.commit()
    finally:
        conn.close()
    if r is None:
        raise LookupError(f"user {user_id} not found")
    return r["timezone"] or None
