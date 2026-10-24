"""Одноразовые коды второго фактора (многопользовательский план P2-04).
Чистые функции, без базы и без сети.

TOTP по RFC 6238 поверх HOTP из RFC 4226: HMAC-SHA1 от номера 30-секундного
шага, 6 цифр. Проверка принимает соседние шаги (±1, расхождение часов
телефона и сервера), а какой шаг совпал -- возвращает: запомнить его и не
пустить тот же код второй раз -- дело вызывающего (application/two_factor.py).

Резервные коды -- 10 случайных строк по 10 знаков из алфавита без похожих
символов (0/o, 1/l/i); в базе только их хэш. Секрет TOTP -- 20 случайных
байт в base32 без «=», как его ждут приложения-аутентификаторы.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PERIOD = 30
DIGITS = 6
WINDOW = 1                      # сколько шагов до и после текущего принимается
SECRET_BYTES = 20               # 160 бит, как советует RFC 4226 для SHA-1
BACKUP_COUNT = 10
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_BACKUP_LEN = 10                # ~49 бит на код


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def secret_bytes(secret: str) -> bytes:
    """base32 в байты; пробелы и регистр не важны, «=» дописывается сам."""
    s = "".join(str(secret or "").split()).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 4226: динамическое усечение HMAC от 8-байтового счётчика."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** digits).zfill(digits)


def step_at(ts: float) -> int:
    return int(ts // PERIOD)


def code_at(secret: str, ts: float) -> str:
    return hotp(secret_bytes(secret), step_at(ts))


def clean_code(code) -> str:
    """Что ввёл человек, без пробелов и дефисов («123 456» = «123456»)."""
    return "".join(ch for ch in str(code or "") if ch.isalnum())


def looks_like_totp(code) -> bool:
    c = clean_code(code)
    return len(c) == DIGITS and c.isdigit()


def matching_step(secret: str, code, ts: float = None, window: int = WINDOW):
    """Номер шага, чей код совпал с введённым, или None. Сравнение за
    постоянное время, все шаги окна проверяются всегда."""
    c = clean_code(code)
    if len(c) != DIGITS or not c.isdigit():
        return None
    key = secret_bytes(secret)
    now = step_at(time.time() if ts is None else ts)
    found = None
    for step in range(now - window, now + window + 1):
        if hmac.compare_digest(hotp(key, step), c) and found is None:
            found = step
    return found


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    """Ссылка otpauth://totp/... -- её понимают Google Authenticator, Aegis,
    1Password и др.; та же строка, что зашивается в QR."""
    label = quote(f"{issuer}:{account}", safe="@:")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                       "digits": DIGITS, "period": PERIOD})
    return f"otpauth://totp/{label}?{query}"


def new_backup_codes(n: int = BACKUP_COUNT) -> list:
    """Коды вида «abcde-fghjk»: дефис только для глаз, при проверке не важен."""
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(_BACKUP_LEN))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def normalize_backup(code) -> str:
    return clean_code(code).lower()
