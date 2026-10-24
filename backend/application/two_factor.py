"""Двухфакторный вход (многопользовательский план P2-04): код из приложения-
аутентификатора (TOTP, domain/totp.py) или одноразовый резервный код.

Включение -- в два шага: setup() создаёт секрет и отдаёт его (строкой и
ссылкой otpauth://), enable() принимает первый код из приложения, включает
защиту, выдаёт 10 резервных кодов -- один раз -- и завершает остальные входы
аккаунта. Секрет хранится зашифрованным OWN_TOKENS_KEY (infrastructure/
secrets.py, тот же MultiFernet, что у каналов и уведомлений; его переводит на
новый ключ `cli.py rotate-secrets`). Без ключа 2FA не включить. Резервные коды
-- только scrypt-хэш (тот же, что у паролей), каждый срабатывает один раз.

Вход: после верного пароля login() у аккаунта с 2FA не выдаёт сессию, а
start_challenge() -- промежуточный токен (в базе только SHA-256), живущий
MFA_CHALLENGE_MINUTES и MFA_MAX_TRIES ошибок; complete_login() по нему и
коду выдаёт сессию. Ошибки кода считаются в тех же счётчиках login_attempts
(email и адрес), что и ошибки пароля (P0-03), а счётчик email при верном
пароле не обнуляется -- иначе перебор кода шёл бы бесплатно. Тот же код
дважды не принимается: запоминается шаг последнего принятого.

Сброс пароля по ссылке (P1-06) 2FA не снимает: после него тоже второй шаг.
Снять 2FA -- самому с паролем и кодом, либо оператору `cli.py disable-2fa`.
NF_ADMIN_2FA=required: администратор без 2FA после входа может только
включить её (session_guard в interfaces/http/api.py).
"""
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import infrastructure.postgres as db
import infrastructure.secrets as SEC
from application import audit as AUD
from application import auth as AUTH
from domain import totp as DT
from domain.i18n import tr

MFA_CHALLENGE_MINUTES = 5
MFA_MAX_TRIES = 5
# часы для кодов TOTP (тесты подставляют свои)
_clock = time.time


class TwoFactorError(ValueError):
    """Неверный ввод или состояние; HTTP отвечает 400."""


class TwoFactorUnavailable(TwoFactorError):
    """На сервере нет (или не тот) OWN_TOKENS_KEY: секрет не сохранить и не прочитать."""


def issuer() -> str:
    return (os.environ.get("NF_TOTP_ISSUER") or "").strip() or "niche-finder"


def admin_required() -> bool:
    """NF_ADMIN_2FA=required: администраторам без 2FA доступна только её настройка."""
    return (os.environ.get("NF_ADMIN_2FA") or "").strip().lower() == "required"


def available() -> bool:
    return SEC.configured()


def _now():
    return datetime.now(timezone.utc)


def _conn_call(conn, fn):
    """fn(conn) на переданном соединении или на своём."""
    if conn is not None:
        return fn(conn)
    own = db.get_conn()
    try:
        return fn(own)
    finally:
        own.close()


def _row(conn, user_id):
    return conn.execute("SELECT secret_enc, enabled_at, last_step FROM user_totp WHERE user_id = ?",
                        (user_id,)).fetchone()


def enabled(user_id: int, conn=None) -> bool:
    return _conn_call(conn, lambda c: bool((r := _row(c, user_id)) and r["enabled_at"]))


def setup_required(user: dict) -> bool:
    """Администратор без 2FA при NF_ADMIN_2FA=required."""
    return bool(user and user.get("isAdmin") and admin_required() and not enabled(user["id"]))


def _codes_left(conn, user_id) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM user_backup_codes WHERE user_id = ? "
                        "AND used_at IS NULL", (user_id,)).fetchone()["n"]


def status(user_id: int, is_admin: bool) -> dict:
    """Что показать в профиле: включена ли, начата ли настройка, сколько
    осталось резервных кодов, можно ли включить на этом сервере."""
    conn = db.get_conn()
    try:
        row, left = _row(conn, user_id), _codes_left(conn, user_id)
    finally:
        conn.close()
    on = bool(row and row["enabled_at"])
    return {"enabled": on, "pending": bool(row and not row["enabled_at"]),
            "enabledAt": row["enabled_at"] if on else None, "backupCodesLeft": left,
            "available": available(), "required": bool(is_admin and admin_required()),
            "issuer": issuer()}


def _secret(row, user_id) -> str:
    try:
        return SEC.decrypt(row["secret_enc"], what=f"user_totp.secret_enc user {user_id}")
    except SEC.SecretsNotConfigured:
        raise TwoFactorUnavailable(tr(
            "Секрет двухфакторной защиты не читается на этом сервере (нет или сменился "
            "OWN_TOKENS_KEY). Войдите резервным кодом или попросите администратора выполнить "
            "cli.py disable-2fa.")) from None


def setup(user_id: int, email: str) -> dict:
    """Новый секрет (прежняя незавершённая настройка заменяется). Возвращает
    его и ссылку otpauth:// -- показываются только сейчас."""
    if not available():
        raise TwoFactorUnavailable(tr(
            "Двухфакторную защиту нельзя включить: на сервере не задан OWN_TOKENS_KEY "
            "(ключ шифрования секретов). Обратитесь к администратору."))
    secret = DT.new_secret()
    conn = db.get_conn()
    try:
        row = _row(conn, user_id)
        if row and row["enabled_at"]:
            raise TwoFactorError(tr("Двухфакторная защита уже включена."))
        conn.execute("INSERT INTO user_totp (user_id, secret_enc, created_at) VALUES (?,?,?) "
                     "ON CONFLICT (user_id) DO UPDATE SET secret_enc = excluded.secret_enc, "
                     "created_at = excluded.created_at, enabled_at = NULL, last_step = NULL",
                     (user_id, SEC.encrypt(secret), db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    return {"secret": secret, "uri": DT.otpauth_uri(secret, email, issuer()),
            "issuer": issuer(), "account": email, "digits": DT.DIGITS, "period": DT.PERIOD}


def _new_backup_codes(conn, user_id) -> list:
    """Прежние резервные коды гаснут, выдаются новые (в базе -- хэши)."""
    codes, now = DT.new_backup_codes(), db.now_iso()
    conn.execute("DELETE FROM user_backup_codes WHERE user_id = ?", (user_id,))
    for code in codes:
        conn.execute("INSERT INTO user_backup_codes (user_id, code_hash, created_at) VALUES (?,?,?)",
                     (user_id, AUTH.hash_password(DT.normalize_backup(code)), now))
    return codes


def enable(user_id: int, code: str, session_id) -> dict:
    """Первый код из приложения подтверждает настройку: 2FA включена, выданы
    резервные коды, другие входы аккаунта завершены, этот считается прошедшим
    второй фактор. API-токены остаются: это отдельный фактор."""
    conn = db.get_conn()
    try:
        row = _row(conn, user_id)
        if row and row["enabled_at"]:
            raise TwoFactorError(tr("Двухфакторная защита уже включена."))
        if not row:
            raise TwoFactorError(tr("Сначала начните настройку: получите секрет для приложения."))
        step = DT.matching_step(_secret(row, user_id), code, _clock())
        if step is None:
            raise TwoFactorError(tr("Неверный код. Проверьте время на телефоне и введите "
                                    "новый код."))
        now = db.now_iso()
        conn.execute("UPDATE user_totp SET enabled_at = ?, last_step = ? WHERE user_id = ?",
                     (now, step, user_id))
        codes = _new_backup_codes(conn, user_id)
        ended = len(conn.execute("DELETE FROM sessions WHERE user_id = ? AND id IS DISTINCT FROM ? "
                                 "RETURNING id", (user_id, session_id)).fetchall())
        if session_id is not None:
            conn.execute("UPDATE sessions SET mfa_at = ? WHERE id = ? AND user_id = ?",
                         (now, session_id, user_id))
        AUD.record(conn, "2fa_enable", actor_id=user_id, target_id=user_id,
                   details={"sessionsEnded": ended})
        conn.commit()
    finally:
        conn.close()
    return {"enabled": True, "backupCodes": codes, "sessionsEnded": ended}


def _totp_ok(conn, user_id, code) -> bool:
    """Код приложения подошёл и его шаг новее последнего принятого (повтор
    того же кода -- нет). Запоминание шага -- одним UPDATE: два параллельных
    запроса с одним кодом не пройдут оба."""
    row = _row(conn, user_id)
    if not row or not row["enabled_at"]:
        return False
    step = DT.matching_step(_secret(row, user_id), code, _clock())
    if step is None or (row["last_step"] is not None and step <= row["last_step"]):
        return False
    return conn.execute("UPDATE user_totp SET last_step = ? WHERE user_id = ? "
                        "AND (last_step IS NULL OR last_step < ?) RETURNING user_id",
                        (step, user_id, step)).fetchone() is not None


def _backup_ok(conn, user_id, code) -> bool:
    """Неиспользованный резервный код подошёл -- и тут же погашен."""
    norm = DT.normalize_backup(code)
    if len(norm) < 8:
        return False
    for r in conn.execute("SELECT id, code_hash FROM user_backup_codes WHERE user_id = ? "
                          "AND used_at IS NULL ORDER BY id", (user_id,)).fetchall():
        if AUTH.verify_password(norm, r["code_hash"]):
            return conn.execute("UPDATE user_backup_codes SET used_at = ? WHERE id = ? "
                                "AND used_at IS NULL RETURNING id",
                                (db.now_iso(), r["id"])).fetchone() is not None
    return False


def _verify(conn, user_id, code):
    """'totp', 'backup' или None. Шесть цифр -- код приложения, иначе резервный."""
    if DT.looks_like_totp(code):
        return "totp" if _totp_ok(conn, user_id, code) else None
    return "backup" if _backup_ok(conn, user_id, code) else None


def _keys(email, client_ip):
    return [("email", AUTH._norm(email))] + ([("ip", client_ip)] if client_ip else [])


def _throttle(conn, keys, now, user_id, step):
    """Пауза после ошибок -- до проверки кода, как у пароля (P0-03)."""
    wait = max(AUTH._wait(conn, kind, key, now) for kind, key in keys)
    if wait:
        AUD.record(conn, "2fa_failed", target_id=user_id, details={"step": step,
                                                                   "reason": "throttled"})
        conn.commit()
        raise AUTH.LoginThrottled(wait)


def _backup_used(conn, user_id, step):
    AUD.record(conn, "2fa_backup_used", actor_id=user_id, target_id=user_id,
               details={"step": step, "left": _codes_left(conn, user_id)})


# ------------------------------------------------------------ вход в два шага

def start_challenge(conn, user_id: int) -> dict:
    """Пароль (или ссылка сброса) верный, у аккаунта 2FA: вместо сессии --
    промежуточный токен второго шага, на conn (коммитит вызывающий)."""
    token, now = secrets.token_urlsafe(32), _now()
    expires = now + timedelta(minutes=MFA_CHALLENGE_MINUTES)
    conn.execute("DELETE FROM mfa_challenges WHERE expires_at < ?", (now.isoformat(),))
    conn.execute("INSERT INTO mfa_challenges (token_hash, user_id, created_at, expires_at) "
                 "VALUES (?,?,?,?)", (AUTH._token_hash(token), user_id, now.isoformat(),
                                      expires.isoformat()))
    return {"mfaRequired": True, "challenge": token, "expiresAt": expires.isoformat()}


def _stale():
    return AUTH.AuthError(tr("Время на ввод кода вышло или вход уже завершён: войдите заново."))


def complete_login(challenge: str, code: str, client_ip: str = None) -> dict:
    """Второй шаг: промежуточный токен и код (приложения или резервный) --
    сессия, как у AUTH.login. Ошибки тормозятся счётчиками входа; после
    MFA_MAX_TRIES ошибок токен гаснет, и вход начинается с пароля."""
    now, th = _now(), AUTH._token_hash(challenge or "")
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT c.user_id, c.expires_at, u.email FROM mfa_challenges c "
            "JOIN users u ON u.id = c.user_id WHERE c.token_hash = ? AND u.disabled_at IS NULL",
            (th,)).fetchone()
        expires = AUTH._ts(row["expires_at"]) if row else None
        if not row or expires is None or expires <= now:
            if row:
                conn.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (th,))
                conn.commit()
            raise _stale()
        uid, email = row["user_id"], row["email"]
        keys = _keys(email, client_ip)
        _throttle(conn, keys, now, uid, "login")
        try:
            method = _verify(conn, uid, code)
        except TwoFactorUnavailable as e:
            raise AUTH.AuthError(str(e)) from None
        if not method:
            tries = conn.execute("UPDATE mfa_challenges SET attempts = attempts + 1 "
                                 "WHERE token_hash = ? RETURNING attempts", (th,)).fetchone()
            if tries and tries["attempts"] >= MFA_MAX_TRIES:
                conn.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (th,))
            AUD.record(conn, "2fa_failed", target_id=uid, ip=client_ip,
                       details={"step": "login"})
            AUTH._failed(conn, keys, now)
            raise AUTH.AuthError(tr("Неверный код."))
        # одноразовый: из двух параллельных успехов сессию получает один
        if not conn.execute("DELETE FROM mfa_challenges WHERE token_hash = ? RETURNING user_id",
                            (th,)).fetchone():
            conn.rollback()
            raise _stale()
        db.clear_login_failures(conn, "email", AUTH._norm(email))
        s = AUTH.start_session(conn, uid)
        mark_session(s["token"], conn=conn)
        AUD.record(conn, "login", actor_id=uid, target_id=uid, ip=client_ip,
                   details={"secondFactor": method})
        if method == "backup":
            _backup_used(conn, uid, "login")
        conn.commit()
        return s
    finally:
        conn.close()


def mark_session(token: str, conn=None) -> None:
    """Сессия прошла второй фактор (вход в два шага, смена пароля из такой
    сессии). Без conn -- на своём соединении, с коммитом."""
    def run(c):
        c.execute("UPDATE sessions SET mfa_at = ? WHERE token_hash = ?",
                  (db.now_iso(), AUTH._token_hash(token)))
        if conn is None:
            c.commit()
    _conn_call(conn, run)


def session_verified(session_id) -> bool:
    if session_id is None:
        return False
    return _conn_call(None, lambda c: bool(
        (r := c.execute("SELECT mfa_at FROM sessions WHERE id = ?", (session_id,)).fetchone())
        and r["mfa_at"]))


# ------------------------------------------------------------ в профиле

def _checked(conn, user_id, password, code, client_ip, step, need_password):
    """Пароль (если нужен) и код из уже открытой сессии; ошибки -- в те же
    счётчики, что у входа. Возвращает 'totp' или 'backup'."""
    now = _now()
    user = conn.execute("SELECT email, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise TwoFactorError(tr("Неверный пароль или код."))
    keys = _keys(user["email"], client_ip)
    _throttle(conn, keys, now, user_id, step)
    if not enabled(user_id, conn=conn):
        raise TwoFactorError(tr("Двухфакторная защита выключена."))
    ok = not need_password or AUTH.verify_password(password or "", user["password_hash"])
    method = _verify(conn, user_id, code) if ok else None
    if not method:
        AUD.record(conn, "2fa_failed", actor_id=user_id, target_id=user_id, ip=client_ip,
                   details={"step": step})
        AUTH._failed(conn, keys, now)
        raise TwoFactorError(tr("Неверный пароль или код.") if need_password
                             else tr("Неверный код."))
    return method


def _drop(conn, user_id):
    conn.execute("DELETE FROM user_totp WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM user_backup_codes WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM mfa_challenges WHERE user_id = ?", (user_id,))


def disable(user_id: int, password: str, code: str, is_admin: bool, client_ip: str = None) -> dict:
    """Выключить самому: нужны и пароль, и код (приложения или резервный).
    При NF_ADMIN_2FA=required администратору нельзя."""
    if is_admin and admin_required():
        raise TwoFactorError(tr("На этом сервере администраторам двухфакторная защита "
                                "обязательна (NF_ADMIN_2FA=required)."))
    conn = db.get_conn()
    try:
        method = _checked(conn, user_id, password, code, client_ip, "disable", need_password=True)
        _drop(conn, user_id)
        AUD.record(conn, "2fa_disable", actor_id=user_id, target_id=user_id,
                   details={"by": "self", "secondFactor": method})
        conn.commit()
    finally:
        conn.close()
    return {"enabled": False}


def regenerate_backup_codes(user_id: int, code: str, client_ip: str = None) -> dict:
    """Новые резервные коды по текущему коду; прежние больше не действуют."""
    conn = db.get_conn()
    try:
        method = _checked(conn, user_id, None, code, client_ip, "backup_codes", need_password=False)
        if method == "backup":
            _backup_used(conn, user_id, "backup_codes")
        codes = _new_backup_codes(conn, user_id)
        AUD.record(conn, "2fa_backup_regenerate", actor_id=user_id, target_id=user_id,
                   details={"count": len(codes)})
        conn.commit()
    finally:
        conn.close()
    return {"backupCodes": codes}


def operator_disable(email: str) -> dict:
    """`cli.py disable-2fa EMAIL`: оператор снимает 2FA (человек потерял
    телефон и коды). Входы аккаунта не трогаются; запись в журнал."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (AUTH._norm(email),)).fetchone()
        if not row:
            raise TwoFactorError(tr("Нет пользователя {email}.", email=AUTH._norm(email)))
        uid = row["id"]
        was = enabled(uid, conn=conn)
        _drop(conn, uid)
        if was:
            AUD.record(conn, "2fa_disable", actor_id=AUD.OPERATOR_ID, target_id=uid,
                       details={"by": "operator"})
        conn.commit()
    finally:
        conn.close()
    return {"userId": uid, "email": AUTH._norm(email), "disabled": was}
