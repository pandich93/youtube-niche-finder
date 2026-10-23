"""Encryption key rotation (P1-12): OWN_TOKENS_KEY encrypts, the keys in
OWN_TOKENS_KEY_OLD only read; `cli.py rotate-secrets` re-encrypts what an old
key reads (a dry run unless --apply); a value no key opens is logged (without
the value) and counted for the admin. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_secrets_rotation.py
"""
import argparse
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet, InvalidToken  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import infrastructure.secrets as SEC  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import notify_settings as NS  # noqa: E402
from application import secrets_rotation as SR  # noqa: E402
from infrastructure.notify.null import NullNotifier  # noqa: E402
from infrastructure.redact import MASK, redact  # noqa: E402
from interfaces.cli import cli  # noqa: E402

PW = "correct horse battery"
REFRESH = "1//refresh-token-that-must-never-leak"
BOT = "123456:bot-token-that-must-never-leak"
HOOK = "https://hooks.example.com/that-must-never-leak"


def _key():
    return Fernet.generate_key().decode()


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def keys(monkeypatch):
    """Two keys: `old` was current before, `new` is current now."""
    old, new = _key(), _key()
    monkeypatch.setenv(SEC.ENV, old)
    monkeypatch.delenv(SEC.OLD_ENV, raising=False)
    conn = db.get_conn()
    for t in ("own_channels", "user_settings", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    return old, new


def _rotate_env(monkeypatch, old, new, *more_old):
    monkeypatch.setenv(SEC.ENV, new)
    monkeypatch.setenv(SEC.OLD_ENV, ",".join([old, *more_old]))


def _store(user_id, channel_id="UC" + "rot".ljust(22, "0"), bot=BOT, hook=None):
    """A connected channel and saved notification settings, encrypted with
    whatever key is current right now."""
    conn = db.get_conn()
    conn.execute("INSERT INTO own_channels (user_id, channel_id, title, token_enc) VALUES (?,?,?,?)",
                 (user_id, channel_id, "Mine", SEC.encrypt(REFRESH)))
    conn.execute("INSERT INTO user_settings (user_id, telegram_token_enc, telegram_chat_id, "
                 "webhook_url_enc) VALUES (?,?,?,?)",
                 (user_id, SEC.encrypt(bot) if bot else None, "42",
                  SEC.encrypt(hook) if hook else None))
    conn.commit()
    conn.close()


def _blobs():
    conn = db.get_conn()
    try:
        out = [bytes(r["token_enc"]) for r in conn.execute(
            "SELECT token_enc FROM own_channels ORDER BY user_id, channel_id").fetchall()]
        for r in conn.execute("SELECT telegram_token_enc, webhook_url_enc FROM user_settings "
                              "ORDER BY user_id").fetchall():
            out += [bytes(b) for b in (r["telegram_token_enc"], r["webhook_url_enc"]) if b]
        return out
    finally:
        conn.close()


def _cli(apply):
    return cli.cmd_rotate_secrets(argparse.Namespace(apply=apply))


# ------------------------------------------------------------ the keys

def test_one_key_works_as_before(keys):
    old, _ = keys
    blob = SEC.encrypt(REFRESH)
    assert SEC.decrypt(blob) == REFRESH
    assert Fernet(old.encode()).decrypt(blob).decode() == REFRESH
    assert SEC.key_state(blob) == SEC.CURRENT


def test_old_key_reads_new_key_writes(keys, monkeypatch):
    old, new = keys
    legacy = SEC.encrypt(REFRESH)
    _rotate_env(monkeypatch, old, new)
    assert SEC.configured()
    assert SEC.decrypt(legacy) == REFRESH          # still readable after the switch
    assert SEC.key_state(legacy) == SEC.OLD
    fresh = SEC.encrypt(BOT)
    assert Fernet(new.encode()).decrypt(fresh).decode() == BOT
    with pytest.raises(InvalidToken):               # the old key never encrypts again
        Fernet(old.encode()).decrypt(fresh)
    rotated = SEC.rotate(legacy)
    assert Fernet(new.encode()).decrypt(rotated).decode() == REFRESH
    assert SEC.key_state(rotated) == SEC.CURRENT


def test_several_old_keys_and_spaces(keys, monkeypatch):
    old, new = keys
    a = SEC.encrypt("from the oldest key")
    middle = _key()
    monkeypatch.setenv(SEC.ENV, middle)
    b = SEC.encrypt("from the middle key")
    monkeypatch.setenv(SEC.ENV, new)
    monkeypatch.setenv(SEC.OLD_ENV, f" {middle} , {old} ,")
    assert SEC.decrypt(a) == "from the oldest key"
    assert SEC.decrypt(b) == "from the middle key"


def test_a_broken_old_key_is_an_error_not_ignored(keys, monkeypatch):
    monkeypatch.setenv(SEC.OLD_ENV, "not-a-fernet-key")
    assert SEC.configured() is False
    with pytest.raises(SEC.SecretsNotConfigured, match=SEC.OLD_ENV):
        SEC.encrypt("x")


def test_unreadable_is_logged_without_the_secret(keys, monkeypatch, caplog):
    blob = SEC.encrypt(REFRESH)
    monkeypatch.setenv(SEC.ENV, _key())             # the key was lost
    with caplog.at_level(logging.ERROR, logger="infrastructure.secrets"):
        with pytest.raises(SEC.SecretUnreadable) as e:
            SEC.decrypt(blob, what="own_channels.token_enc user 7")
    assert isinstance(e.value, SEC.SecretsNotConfigured)     # old handlers still catch it
    assert SEC.key_state(blob) == SEC.UNREADABLE
    text = caplog.text
    assert "own_channels.token_enc user 7" in text and SEC.OLD_ENV in text
    assert REFRESH not in text and REFRESH not in str(e.value)


def test_redact_masks_every_old_key(keys, monkeypatch):
    old, new = keys
    other = _key()
    _rotate_env(monkeypatch, old, new, other)
    line = f"boom {new} {old} {other}"
    assert redact(line) == f"boom {MASK} {MASK} {MASK}"


# ------------------------------------------------------------ rotate-secrets

def test_rotate_secrets_dry_run_changes_nothing(keys, monkeypatch, capsys):
    old, new = keys
    u = AUTH.create_user("rot@example.com", PW)
    _store(1, hook=HOOK)
    _store(u, bot=None, hook=HOOK)
    _rotate_env(monkeypatch, old, new)
    before = _blobs()
    assert _cli(apply=False) == 0
    assert _blobs() == before
    captured = capsys.readouterr()
    assert '"rotated": 5' in captured.out and '"applied": false' in captured.out
    assert "--apply" in captured.err
    for secret in (REFRESH, BOT, HOOK, old, new):
        assert secret not in captured.out + captured.err


def test_rotate_secrets_apply_moves_everything_to_the_new_key(keys, monkeypatch, capsys):
    old, new = keys
    u = AUTH.create_user("rot2@example.com", PW)
    _store(1)
    _store(u, bot=None, hook=HOOK)
    _rotate_env(monkeypatch, old, new)
    assert _cli(apply=True) == 0
    assert '"rotated": 4' in capsys.readouterr().out
    current = Fernet(new.encode())
    assert sorted(current.decrypt(b).decode() for b in _blobs()) == sorted(
        [REFRESH, REFRESH, BOT, HOOK])
    # the old key can go now
    monkeypatch.delenv(SEC.OLD_ENV)
    res = SR.rotate(apply=True)
    assert res["rotated"] == 0 and res["current"] == 4 and res["unreadable"] == 0
    assert NS.target_name(user_id=u) == "webhook"


def test_rotate_secrets_reports_unreadable_and_fails(keys, monkeypatch, capsys, caplog):
    old, new = keys
    _store(1)
    monkeypatch.setenv(SEC.ENV, _key())             # a key nobody kept
    _store(AUTH.create_user("lost@example.com", PW), channel_id="UC" + "lost".ljust(22, "0"))
    _rotate_env(monkeypatch, old, new)
    with caplog.at_level(logging.ERROR):
        assert _cli(apply=True) == 1
    captured = capsys.readouterr()
    assert '"rotated": 2' in captured.out and '"unreadable": 2' in captured.out
    assert "OWN_TOKENS_KEY_OLD" in captured.err
    assert "cannot be decrypted" in caplog.text
    assert REFRESH not in caplog.text + captured.out + captured.err


def test_rotate_secrets_without_a_key_is_an_error(keys, monkeypatch, capsys):
    monkeypatch.delenv(SEC.ENV)
    assert _cli(apply=False) == 1
    assert SEC.ENV in capsys.readouterr().err


def test_rotation_skips_a_row_saved_meanwhile(keys, monkeypatch):
    old, new = keys
    _store(1, bot=None)
    _rotate_env(monkeypatch, old, new)
    real_rotate = SEC.rotate

    def save_then_rotate(blob):
        conn = db.get_conn()          # the user reconnects while rotation runs
        conn.execute("UPDATE own_channels SET token_enc = ?", (SEC.encrypt("newer"),))
        conn.commit()
        conn.close()
        return real_rotate(blob)
    monkeypatch.setattr(SEC, "rotate", save_then_rotate)
    res = SR.rotate(apply=True)
    assert res["changedMeanwhile"] == 1 and res["rotated"] == 0
    assert SEC.decrypt(_blobs()[0]) == "newer"


# ------------------------------------------------------------ visible to the admin

def test_health_counts_current_old_unreadable(keys, monkeypatch):
    old, new = keys
    _store(1)                                       # 2 values on `old`
    _rotate_env(monkeypatch, old, new)
    _store(AUTH.create_user("h@example.com", PW), bot=None, hook=HOOK)   # 2 on `new`
    h = SR.health()
    assert (h["total"], h["current"], h["old"], h["unreadable"]) == (4, 2, 2, 0)
    monkeypatch.delenv(SEC.OLD_ENV)                 # the old key dropped too early
    h = SR.health()
    assert (h["current"], h["old"], h["unreadable"]) == (2, 0, 2)
    monkeypatch.delenv(SEC.ENV)                     # no key at all: nothing reads
    h = SR.health()
    assert h["configured"] is False and h["unreadable"] == 4


def test_admin_summary_shows_unreadable_count(keys, monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    _store(1)
    monkeypatch.setenv(SEC.ENV, _key())             # lost
    AUTH.create_user("boss-sec@example.com", PW, is_admin=True)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": "boss-sec@example.com",
                                           "password": PW}).status_code == 200
    body = c.get("/api/admin/users").json()
    assert body["secrets"]["unreadable"] == 2 and body["secrets"]["total"] == 2
    assert REFRESH not in str(body) and BOT not in str(body)


def test_notifier_with_unreadable_secret_logs_and_stays_quiet(keys, monkeypatch, caplog):
    u = AUTH.create_user("n@example.com", PW)
    _store(u)
    monkeypatch.setenv(SEC.ENV, _key())
    with caplog.at_level(logging.ERROR):
        assert isinstance(NS.notifier_for(user_id=u), NullNotifier)
    assert f"user_settings.telegram_token_enc user {u}" in caplog.text
    assert BOT not in caplog.text


def test_notifier_without_any_key_logs(keys, monkeypatch, caplog):
    u = AUTH.create_user("n2@example.com", PW)
    _store(u)
    monkeypatch.delenv(SEC.ENV)
    with caplog.at_level(logging.ERROR, logger="application.notify_settings"):
        assert isinstance(NS.notifier_for(user_id=u), NullNotifier)
    assert "cannot be read" in caplog.text and SEC.ENV in caplog.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
