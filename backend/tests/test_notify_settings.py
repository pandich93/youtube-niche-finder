"""Per-user alert delivery (plan 15, sub-stage 5.9): each user's own Telegram
or webhook and mode, secrets encrypted, user webhooks kept off private
networks, and every user alerted only about their own watchlist. User 1
without saved settings keeps .env. Notifiers are stubs -- no network.
Run with pytest, or directly: python3 tests/test_notify_settings.py
"""
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import alerts as AL  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import channel_tracking as T  # noqa: E402
from application import digest as DG  # noqa: E402
from application import notify_settings as NS  # noqa: E402
from infrastructure.notify import factory as notify_factory  # noqa: E402
from infrastructure.notify import urlguard  # noqa: E402
from infrastructure.notify.null import NullNotifier  # noqa: E402

PW = "correct horse battery"
BOT = "123456:secret-bot-token"
CH_A, CH_B = "UC" + "notifya".ljust(22, "0"), "UC" + "notifyb".ljust(22, "0")


class Sent:
    def __init__(self):
        self.by_chat = {}


def setup_module(_=None):
    db.init_db()


@pytest.fixture
def ab(monkeypatch):
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())
    conn = db.get_conn()
    for t in ("user_settings", "alert_deliveries", "tracked_channels", "events", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.execute("DELETE FROM meta WHERE key LIKE 'digest_%'")
    conn.commit()
    conn.close()
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: NullNotifier())
    monkeypatch.setattr(AL, "NOTIFY_MODE", "instant")
    return AUTH.create_user("a@example.com", PW), AUTH.create_user("b@example.com", PW)


def _public_dns(monkeypatch, ip="93.184.216.34"):
    monkeypatch.setattr(urlguard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(socket.AF_INET, 0, 0, "", (ip, port))])


# ------------------------------------------------------------ url guard

@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254", "::1",
                                "172.18.0.3"])
def test_webhooks_to_private_networks_are_refused(monkeypatch, ip):
    _public_dns(monkeypatch, ip)
    with pytest.raises(urlguard.UnsafeUrl):
        urlguard.check_public_https("https://hooks.example.com/x")


def test_only_https_without_credentials(monkeypatch):
    _public_dns(monkeypatch)
    for bad in ("http://hooks.example.com/x", "https://user:pw@hooks.example.com/x", "ftp://x", ""):
        with pytest.raises(urlguard.UnsafeUrl):
            urlguard.check_public_https(bad)
    assert urlguard.check_public_https("https://hooks.example.com/x") == "https://hooks.example.com/x"


def test_a_saved_webhook_is_checked_again_at_send_time(ab, monkeypatch):
    a, _ = ab
    _public_dns(monkeypatch)
    NS.save(a, webhook_url="https://hooks.example.com/x")
    _public_dns(monkeypatch, "10.0.0.1")                     # DNS changed after saving
    posted = []
    monkeypatch.setattr("infrastructure.notify.webhook.httpx.post", lambda *a, **k: posted.append(a))
    assert NS.notifier_for(a).send("hi") is False and posted == []


# ------------------------------------------------------------ settings

def test_secrets_are_encrypted_and_never_read_back(ab):
    a, _ = ab
    out = NS.save(a, telegram_bot_token=BOT, telegram_chat_id="42", mode="both")
    assert out == {"source": "settings", "telegram": True, "telegramChatId": "42", "webhook": False,
                   "mode": "both", "updatedAt": out["updatedAt"]}
    conn = db.get_conn()
    raw = repr([dict(r) for r in conn.execute("SELECT * FROM user_settings").fetchall()])
    conn.close()
    assert BOT not in raw


def test_bad_mode_and_missing_key_are_errors(ab, monkeypatch):
    a, _ = ab
    with pytest.raises(NS.SettingsError):
        NS.save(a, mode="loud")
    monkeypatch.delenv("OWN_TOKENS_KEY")
    with pytest.raises(NS.SettingsError):
        NS.save(a, telegram_bot_token=BOT)


def test_user_1_without_settings_keeps_env(ab, monkeypatch):
    assert NS.get(1)["source"] == "env" and NS.mode_for(1) == "instant"
    monkeypatch.setattr(AL, "NOTIFY_MODE", "digest")
    assert NS.mode_for(1) == "digest"
    a, _ = ab
    assert NS.get(a)["source"] == "none" and isinstance(NS.notifier_for(a), NullNotifier)


def test_clearing_drops_back_to_env_for_user_1(ab):
    NS.save(1, telegram_bot_token=BOT, telegram_chat_id="1")
    assert NS.get(1)["source"] == "settings"
    assert NS.save(1, clear=True)["source"] == "env"


# ------------------------------------------------------------ delivery

def _stub_telegram(monkeypatch, sent):
    class _Tg:
        def __init__(self, token, chat_id):
            self.chat_id = chat_id

        def send(self, text):
            sent.setdefault(self.chat_id, []).append(text)
            return True
    monkeypatch.setattr(NS, "TelegramNotifier", _Tg)


def _outlier(channel, vid):
    conn = db.get_conn()
    AL._emit(conn, "outlier", vid, {"videoId": vid, "channelId": channel, "title": vid,
                                    "outlierScore": 5, "views": 1000})
    conn.commit()
    conn.close()


def test_each_user_is_alerted_about_their_own_channels_once(ab, monkeypatch):
    a, b = ab
    sent = {}
    _stub_telegram(monkeypatch, sent)
    NS.save(a, telegram_bot_token=BOT, telegram_chat_id="chat-a")
    NS.save(b, telegram_bot_token=BOT, telegram_chat_id="chat-b")
    T.track(CH_A, user_id=a)
    T.track(CH_B, user_id=b)
    T.track(CH_A, user_id=b)
    _outlier(CH_A, "va")
    _outlier(CH_B, "vb")
    AL.deliver_all()
    assert len(sent["chat-a"]) == 1 and "va" in sent["chat-a"][0]
    assert len(sent["chat-b"]) == 2
    AL.deliver_all()                                          # nothing twice
    assert len(sent["chat-a"]) == 1 and len(sent["chat-b"]) == 2


def test_untracking_a_channel_stops_its_alerts_but_keeps_the_feed(ab, monkeypatch):
    a, _ = ab
    sent = {}
    _stub_telegram(monkeypatch, sent)
    NS.save(a, telegram_bot_token=BOT, telegram_chat_id="chat-a")
    T.track(CH_A, user_id=a)
    T.untrack(CH_A, user_id=a)
    _outlier(CH_A, "va")
    AL.deliver_all()
    assert sent == {}
    assert [e["refId"] for e in AL.list_events(user_id=a)] == ["va"]    # history still there


def test_the_worker_syncs_every_users_own_channels(ab, monkeypatch):
    a, b = ab
    from application import own_channels as OWN
    conn = db.get_conn()
    conn.execute("DELETE FROM own_channels")
    for uid in (a, b):
        conn.execute("INSERT INTO own_channels (user_id, channel_id) VALUES (?, ?)", (uid, f"UC{uid}"))
    conn.commit()
    conn.close()
    seen = []
    monkeypatch.setattr(OWN, "sync", lambda user_id=1, today=None, **kw: seen.append(user_id) or {})
    OWN.sync_all()
    assert sorted(seen) == sorted([a, b])


def test_mode_off_and_digest_skip_instant_alerts(ab, monkeypatch):
    a, _ = ab
    sent = {}
    _stub_telegram(monkeypatch, sent)
    NS.save(a, telegram_bot_token=BOT, telegram_chat_id="chat-a", mode="off")
    T.track(CH_A, user_id=a)
    _outlier(CH_A, "va")
    assert AL.deliver(user_id=a)["skipped"] is True and sent == {}
    NS.save(a, mode="digest")
    assert AL.deliver(user_id=a)["skipped"] is True


def test_digests_go_to_users_who_asked_for_them(ab, monkeypatch):
    a, b = ab
    sent = {}
    _stub_telegram(monkeypatch, sent)
    NS.save(a, telegram_bot_token=BOT, telegram_chat_id="chat-a", mode="digest")
    NS.save(b, telegram_bot_token=BOT, telegram_chat_id="chat-b", mode="instant")
    T.track(CH_A, user_id=a)
    _outlier(CH_A, "va")
    assert DG.digest_wanted() is True
    out = DG.send_all_digests(now=__import__("datetime").datetime.now().astimezone().replace(hour=23))
    assert set(out) == {str(a)} and out[str(a)]["sent"] is True
    assert "chat-a" in sent and "chat-b" not in sent


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
