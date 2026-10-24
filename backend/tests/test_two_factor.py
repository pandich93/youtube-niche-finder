"""Двухфакторный вход (многопользовательский план P2-04): TOTP по эталонным
векторам RFC 6238, повтор кода отклоняется, вход в два шага, резервный код
одноразовый, перебор кода тормозится как перебор пароля, администратор без
2FA при NF_ADMIN_2FA=required ограничен, секрет хранится зашифрованным,
сброс пароля по ссылке 2FA не снимает, API-токен -- только из сессии со
вторым фактором. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_two_factor.py
"""
import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.secrets as SEC  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import secrets_rotation as SR  # noqa: E402
from application import signup as SU  # noqa: E402
from application import two_factor as TF  # noqa: E402
from domain import login_throttle as LT  # noqa: E402
from domain import totp as DT  # noqa: E402
from interfaces.cli import cli  # noqa: E402

PW = "correct horse battery"
T0 = 1_900_000_020.0                # начало 30-секундного шага


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv(SEC.ENV, Fernet.generate_key().decode())
    monkeypatch.delenv(SEC.OLD_ENV, raising=False)
    monkeypatch.delenv("NF_ADMIN_2FA", raising=False)
    conn = db.get_conn()
    for t in ("sessions", "api_tokens", "login_attempts", "audit_log", "user_totp",
              "user_backup_codes", "mfa_challenges", "email_tokens"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


@pytest.fixture
def clock(monkeypatch):
    now = [T0]
    monkeypatch.setattr(TF, "_clock", lambda: now[0])
    return now


def _client():
    return TestClient(api.app, headers={"X-NF-Client": "tests"})


def _signed_in(email="ann@example.com", admin=False):
    uid = AUTH.create_user(email, PW, is_admin=admin)
    c = _client()
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return uid, c


def _turn_on(c, clock):
    """Включить 2FA в сессии c: секрет и резервные коды."""
    r = c.post("/api/auth/2fa/setup")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = c.post("/api/auth/2fa/enable", json={"code": DT.code_at(secret, clock[0])})
    assert r.status_code == 200, r.text
    return secret, r.json()["backupCodes"]


def _events(event):
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM audit_log WHERE event = ? ORDER BY id", (event,)).fetchall()
    conn.close()
    return rows


# ------------------------------------------------------------ RFC 6238

# приложение B RFC 6238: 8 цифр, ключи-ASCII под каждый алгоритм
_SEEDS = {hashlib.sha1: b"12345678901234567890",
          hashlib.sha256: b"12345678901234567890123456789012",
          hashlib.sha512: b"1234567890" * 6 + b"1234"}
_VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("ts,sha1,sha256,sha512", _VECTORS)
def test_rfc6238_reference_vectors(ts, sha1, sha256, sha512):
    for digest, want in ((hashlib.sha1, sha1), (hashlib.sha256, sha256), (hashlib.sha512, sha512)):
        assert DT.hotp(_SEEDS[digest], DT.step_at(ts), digits=8, digest=digest) == want


def test_six_digit_code_and_window():
    secret = DT.new_secret()
    assert len(secret) == 32 and DT.secret_bytes(secret.lower()) == DT.secret_bytes(secret)
    code = DT.code_at(secret, T0)
    assert len(code) == 6 and code.isdigit()
    # base32-секрет RFC: «12345678901234567890»
    assert DT.code_at("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", 59) == "287082"
    assert DT.matching_step(secret, code, T0) == DT.step_at(T0)
    assert DT.matching_step(secret, code[:3] + " " + code[3:], T0 + 30) == DT.step_at(T0)   # ±1 шаг
    assert DT.matching_step(secret, code, T0 - 30) == DT.step_at(T0)
    assert DT.matching_step(secret, code, T0 + 60) is None                                   # ±2 -- нет
    assert DT.matching_step(secret, "abcdef", T0) is None


def test_otpauth_uri():
    uri = DT.otpauth_uri("JBSWY3DPEHPK3PXP", "ann@example.com", "niche-finder")
    assert uri.startswith("otpauth://totp/niche-finder:ann@example.com?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri and "issuer=niche-finder" in uri
    assert "digits=6" in uri and "period=30" in uri


def test_backup_codes_shape():
    codes = DT.new_backup_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    assert DT.normalize_backup(" ABCDE-FGHJK ") == "abcdefghjk"


# ------------------------------------------------------------ включение

def test_without_the_key_2fa_cannot_be_turned_on(monkeypatch):
    _, c = _signed_in()
    monkeypatch.delenv(SEC.ENV)
    r = c.post("/api/auth/2fa/setup")
    assert r.status_code == 409 and "OWN_TOKENS_KEY" in r.json()["detail"]
    assert c.get("/api/auth/2fa").json()["available"] is False


def test_the_secret_is_stored_encrypted(clock):
    uid, c = _signed_in()
    secret, _ = _turn_on(c, clock)
    conn = db.get_conn()
    row = conn.execute("SELECT secret_enc FROM user_totp WHERE user_id = ?", (uid,)).fetchone()
    codes = conn.execute("SELECT code_hash FROM user_backup_codes WHERE user_id = ?", (uid,)).fetchall()
    conn.close()
    blob = bytes(row["secret_enc"])
    assert secret.encode() not in blob and SEC.decrypt(blob) == secret
    assert len(codes) == 10 and all(r["code_hash"].startswith("scrypt$") for r in codes)
    # rotate-secrets видит секрет второго фактора
    assert SR.health()["total"] == 1


def test_enabling_needs_a_right_code_and_ends_other_sessions(clock):
    uid, c = _signed_in()
    other = _client()
    other.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    secret = c.post("/api/auth/2fa/setup").json()["secret"]
    assert c.get("/api/auth/2fa").json()["pending"] is True
    bad = c.post("/api/auth/2fa/enable", json={"code": "000000" if DT.code_at(secret, T0) != "000000"
                                                else "111111"})
    assert bad.status_code == 400 and c.get("/api/auth/2fa").json()["enabled"] is False
    r = c.post("/api/auth/2fa/enable", json={"code": DT.code_at(secret, T0)})
    assert r.status_code == 200 and r.json()["sessionsEnded"] == 1 and len(r.json()["backupCodes"]) == 10
    assert other.get("/api/stats").status_code == 401                    # другой вход завершён
    assert c.get("/api/stats").status_code == 200
    st = c.get("/api/auth/2fa").json()
    assert st["enabled"] and st["backupCodesLeft"] == 10
    assert _events("2fa_enable")[0]["target_id"] == uid


# ------------------------------------------------------------ вход в два шага

def test_sign_in_takes_two_steps_and_a_code_works_once(clock):
    uid, c = _signed_in()
    secret, _ = _turn_on(c, clock)
    clock[0] += 30
    fresh = _client()
    r = fresh.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert r.status_code == 200 and r.json()["mfaRequired"] is True
    assert "set-cookie" not in r.headers                                 # сессии ещё нет
    assert fresh.get("/api/stats").status_code == 401
    challenge, code = r.json()["challenge"], DT.code_at(secret, clock[0])
    r = fresh.post("/api/auth/login/2fa", json={"challenge": challenge, "code": code})
    assert r.status_code == 200, r.text
    assert "nf_session" in r.headers["set-cookie"]
    assert fresh.get("/api/stats").status_code == 200
    # промежуточный токен одноразовый
    assert fresh.post("/api/auth/login/2fa", json={"challenge": challenge, "code": code}).status_code == 401
    # тот же код в новом входе -- повтор, отклоняется
    again = _client()
    ch2 = again.post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).json()["challenge"]
    r = again.post("/api/auth/login/2fa", json={"challenge": ch2, "code": code})
    assert r.status_code == 401 and again.get("/api/stats").status_code == 401
    assert _events("2fa_failed")[-1]["target_id"] == uid
    assert _events("login")[-1]["details"]["secondFactor"] == "totp"


def test_an_expired_challenge_is_refused(clock):
    _, c = _signed_in()
    secret, _ = _turn_on(c, clock)
    ch = _client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).json()
    conn = db.get_conn()
    conn.execute("UPDATE mfa_challenges SET expires_at = '2000-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    clock[0] += 30
    with pytest.raises(AUTH.AuthError):
        TF.complete_login(ch["challenge"], DT.code_at(secret, clock[0]))


def test_a_backup_code_works_once(clock):
    uid, c = _signed_in()
    _, codes = _turn_on(c, clock)
    for expected in (200, 401):
        x = _client()
        ch = x.post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).json()["challenge"]
        r = x.post("/api/auth/login/2fa", json={"challenge": ch, "code": codes[0].upper()})
        assert r.status_code == expected, r.text
    used = _events("2fa_backup_used")
    assert len(used) == 1 and used[0]["details"]["left"] == 9
    assert c.get("/api/auth/2fa").json()["backupCodesLeft"] == 9


def test_guessing_codes_is_throttled_like_passwords(clock):
    _, c = _signed_in()
    secret, _ = _turn_on(c, clock)
    clock[0] += 30
    good = DT.code_at(secret, clock[0])
    wrong = "000000" if good != "000000" else "111111"
    x = _client()
    ch = x.post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).json()["challenge"]
    for _ in range(LT.MAX_EMAIL_FAILS):
        assert x.post("/api/auth/login/2fa", json={"challenge": ch, "code": wrong}).status_code == 401
    # после MFA_MAX_TRIES ошибок промежуточный токен погас, а вход по паролю -- пауза
    assert x.post("/api/auth/login/2fa", json={"challenge": ch, "code": good}).status_code in (401, 429)
    r = x.post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert x.get("/api/stats").status_code == 401


def test_a_right_password_does_not_reset_the_counter(clock):
    _, c = _signed_in()
    _turn_on(c, clock)
    conn = db.get_conn()
    db.record_login_failure(conn, "email", "ann@example.com", db.now_iso(), "2000-01-01")
    conn.commit()
    conn.close()
    _client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    conn = db.get_conn()
    assert db.login_failures(conn, "email", "ann@example.com")[0] == 1
    conn.close()


# ------------------------------------------------------------ сброс, токены, выключение

def test_a_reset_by_link_keeps_2fa(clock):
    uid, c = _signed_in()
    _turn_on(c, clock)
    conn = db.get_conn()
    token = SU._issue(conn, "reset", 1, SU._now(), user_id=uid)
    conn.commit()
    conn.close()
    x = _client()
    r = x.post("/api/auth/password/reset", json={"token": token, "password": "a brand new secret"})
    assert r.status_code == 200 and r.json()["mfaRequired"] is True
    assert "set-cookie" not in r.headers and x.get("/api/stats").status_code == 401
    assert TF.enabled(uid)


def test_cli_set_password_keeps_2fa_and_disable_2fa_is_logged(clock, monkeypatch, capsys):
    uid, c = _signed_in()
    _turn_on(c, clock)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("another long secret\n"))
    monkeypatch.setattr(sys, "argv", ["cli.py", "set-password", "ann@example.com", "--password-stdin"])
    with pytest.raises(SystemExit):
        cli.main()
    assert '"twoFactorStillOn": true' in capsys.readouterr().out and TF.enabled(uid)
    monkeypatch.setattr(sys, "argv", ["cli.py", "disable-2fa", "ann@example.com"])
    with pytest.raises(SystemExit):
        cli.main()
    assert '"disabled": true' in capsys.readouterr().out and not TF.enabled(uid)
    ev = _events("2fa_disable")
    assert ev[-1]["actor_id"] == 0 and ev[-1]["details"]["by"] == "operator"


def test_tokens_need_a_session_that_passed_2fa(clock):
    uid, c = _signed_in()
    _turn_on(c, clock)
    assert c.post("/api/auth/tokens", json={"name": "ext"}).status_code == 200
    # сессия без второго фактора (как выданная до включения) токен не создаёт
    conn = db.get_conn()
    s = AUTH.start_session(conn, uid)
    conn.commit()
    conn.close()
    old = _client()
    old.cookies.set("nf_session", s["token"])
    r = old.post("/api/auth/tokens", json={"name": "ext2"})
    assert r.status_code == 403
    # а сам токен второго фактора не просит
    token = c.post("/api/auth/tokens", json={"name": "ext3"}).json()["token"]
    ext = TestClient(api.app, headers={"X-NF-Client": "ext", "Authorization": f"Bearer {token}"})
    assert ext.get("/api/stats").status_code == 200


def test_a_password_change_keeps_the_second_factor_of_the_session(clock):
    _, c = _signed_in()
    _turn_on(c, clock)
    r = c.post("/api/auth/password", json={"currentPassword": PW, "password": "another long secret"})
    assert r.status_code == 200
    assert c.post("/api/auth/tokens", json={"name": "ext"}).status_code == 200


def test_disabling_needs_the_password_and_a_code(clock):
    uid, c = _signed_in()
    secret, codes = _turn_on(c, clock)
    clock[0] += 30
    code = DT.code_at(secret, clock[0])
    r = c.post("/api/auth/2fa/disable", json={"password": "wrong password!!", "code": code})
    assert r.status_code == 400 and TF.enabled(uid)
    assert _events("2fa_failed")[-1]["details"]["step"] == "disable"
    r = c.post("/api/auth/2fa/disable", json={"password": PW, "code": code})
    assert r.status_code == 200 and not TF.enabled(uid)
    assert _events("2fa_disable")[-1]["details"]["by"] == "self"
    # без 2FA вход снова в один шаг
    r = _client().post("/api/auth/login", json={"email": "ann@example.com", "password": PW})
    assert r.status_code == 200 and "mfaRequired" not in r.json()


def test_backup_codes_can_be_regenerated(clock):
    _, c = _signed_in()
    secret, codes = _turn_on(c, clock)
    clock[0] += 30
    r = c.post("/api/auth/2fa/backup-codes", json={"code": DT.code_at(secret, clock[0])})
    assert r.status_code == 200
    fresh = r.json()["backupCodes"]
    assert len(fresh) == 10 and not set(fresh) & set(codes)
    x = _client()
    ch = x.post("/api/auth/login", json={"email": "ann@example.com", "password": PW}).json()["challenge"]
    assert x.post("/api/auth/login/2fa", json={"challenge": ch, "code": codes[1]}).status_code == 401
    assert _events("2fa_backup_regenerate")


# ------------------------------------------------------------ администраторы

def test_admin_without_2fa_is_limited_when_required(clock, monkeypatch):
    _, c = _signed_in("boss@example.com", admin=True)
    assert c.get("/api/stats").status_code == 200                        # флаг не задан -- как раньше
    monkeypatch.setenv("NF_ADMIN_2FA", "required")
    r = c.get("/api/stats")
    assert r.status_code == 403 and r.json()["error"] == "TwoFactorRequired"
    assert c.get("/api/auth/me").json()["twoFactor"]["setupRequired"] is True
    assert c.post("/api/auth/tokens", json={"name": "x"}).status_code == 403
    assert c.get("/api/auth/2fa").json()["required"] is True
    secret, _ = _turn_on(c, clock)
    assert c.get("/api/stats").status_code == 200
    assert c.get("/api/auth/me").json()["twoFactor"]["setupRequired"] is False
    clock[0] += 30
    r = c.post("/api/auth/2fa/disable", json={"password": PW, "code": DT.code_at(secret, clock[0])})
    assert r.status_code == 400                                          # обязательна -- не снять
    # обычного пользователя флаг не касается
    _, ann = _signed_in()
    assert ann.get("/api/stats").status_code == 200


def test_time_based_codes_use_the_real_clock_by_default():
    secret = DT.new_secret()
    assert DT.matching_step(secret, DT.code_at(secret, time.time())) is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
