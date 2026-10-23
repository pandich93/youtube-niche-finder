"""Interface language of what the worker sends (i18n plan): alerts and the
digest are written in each recipient's saved language within ONE worker run,
and the public /privacy and /terms pages follow ?lang= or the request's
language. No network -- notifiers are stubs. Run with pytest, or directly:
python3 tests/test_i18n_notify.py
"""
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import alerts, digest, locale_prefs  # noqa: E402
from application import notify_settings as NS  # noqa: E402
from domain import i18n  # noqa: E402

RU_USER, EN_USER = 1, 4242
CHANNEL = "UCi18nnotify"


def setup_module(_=None):
    db.init_db()


def teardown_module(_=None):
    # leave no saved language behind for the tests that run after this module
    conn = db.get_conn()
    conn.execute("DELETE FROM user_prefs WHERE user_id IN (?, ?)", (RU_USER, EN_USER))
    conn.execute("DELETE FROM tracked_channels WHERE channel_id = ?", (CHANNEL,))
    conn.commit()
    conn.close()


class _Stub:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def _prepare(monkeypatch, mode):
    conn = db.get_conn()
    for table in ("events", "alert_deliveries", "user_prefs"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM meta WHERE key LIKE 'digest_last_sent_date%'")
    for uid in (RU_USER, EN_USER):
        conn.execute("INSERT INTO tracked_channels (user_id, channel_id, added_at, active) "
                     "VALUES (?, ?, ?, 1) ON CONFLICT DO NOTHING", (uid, CHANNEL, db.now_iso()))
    alerts._emit(conn, "outlier", "vi18n", {"videoId": "vi18n", "channelId": CHANNEL,
                                            "title": "Some <video>", "outlierScore": 7.5,
                                            "views": 12000})
    alerts._emit(conn, "channel_gone", CHANNEL, {"channelId": CHANNEL, "title": "Gone ch",
                                                 "subscribers": 10, "views": 20, "videoCount": 3,
                                                 "goneSince": "2026-10-01T00:00:00Z"})
    conn.commit()
    conn.close()
    locale_prefs.save("ru", user_id=RU_USER)
    locale_prefs.save("en", user_id=EN_USER)

    stubs = {RU_USER: _Stub(), EN_USER: _Stub()}
    monkeypatch.setattr(NS, "recipients", lambda: [RU_USER, EN_USER])
    monkeypatch.setattr(NS, "mode_for", lambda uid=RU_USER: mode)
    monkeypatch.setattr(NS, "notifier_for", lambda uid=RU_USER: stubs[uid])
    monkeypatch.setattr(NS, "target_name", lambda uid=RU_USER: "webhook")
    return stubs


def test_each_user_gets_alerts_in_their_own_language_in_one_run(monkeypatch):
    stubs = _prepare(monkeypatch, "instant")
    out = alerts.deliver_all()
    assert out[str(RU_USER)]["sent"] == 2 and out[str(EN_USER)]["sent"] == 2

    ru, en = "\n".join(stubs[RU_USER].sent), "\n".join(stubs[EN_USER].sent)
    assert "12000 просмотров" in ru and "Канал больше не доступен" in ru and ">дашборд<" in ru
    assert "12000 views" in en and "Channel no longer available" in en and ">dashboard<" in en
    assert "просмотров" not in en
    # data is escaped exactly as before, in both languages
    assert "Some &lt;video&gt;" in ru and "Some &lt;video&gt;" in en
    # the worker leaves no locale behind
    assert i18n.current_locale() == i18n.default_locale()


def test_the_summary_line_follows_the_recipient_too(monkeypatch):
    stubs = _prepare(monkeypatch, "instant")
    alerts.deliver_all(max_per_cycle=0)
    assert "И ещё 2 алертов" in stubs[RU_USER].sent[-1]
    assert "And 2 more alerts" in stubs[EN_USER].sent[-1]
    assert "open the dashboard" in stubs[EN_USER].sent[-1]


def test_each_user_gets_the_digest_in_their_own_language(monkeypatch):
    stubs = _prepare(monkeypatch, "digest")
    out = digest.send_all_digests(now=datetime(2026, 10, 23, 23, 0).astimezone())
    assert out[str(RU_USER)]["sent"] and out[str(EN_USER)]["sent"]

    ru, en = stubs[RU_USER].sent[0], stubs[EN_USER].sent[0]
    assert "за сутки" in ru and "Новые outlier-видео" in ru and "• канал Gone ch" in ru
    assert "last 24 hours" in en and "New outlier videos" in en and "• channel Gone ch" in en
    assert "открыть дашборд" in ru and "open the dashboard" in en


def test_the_digest_send_path_beats_the_request_language(monkeypatch):
    """POST /api/digest/send from an English dashboard still writes to a
    Russian-speaking recipient in Russian: the text goes to their Telegram."""
    stubs = _prepare(monkeypatch, "digest")
    with i18n.use_locale("en"):
        digest.send_digest(force=True, user_id=RU_USER)
    assert "за сутки" in stubs[RU_USER].sent[0]


def test_every_digest_section_header_has_an_english_translation():
    catalog = i18n._catalog("en")
    missing = [h for _key, h, _fn in digest.SECTIONS if h not in catalog]
    assert not missing, missing


# --------------------------------------------------------------- legal pages

client = TestClient(api.app)


def test_privacy_lang_param_picks_english():
    r = client.get("/privacy?lang=en", headers={"accept-language": "ru"})
    assert r.status_code == 200
    assert '<html lang="en">' in r.text and "<h1>Privacy policy</h1>" in r.text
    assert "Limited Use" in r.text and 'href="/privacy?lang=ru"' in r.text


def test_privacy_follows_accept_language():
    r = client.get("/privacy", headers={"accept-language": "ru-RU,ru;q=0.9"})
    assert '<html lang="ru">' in r.text and "Политика конфиденциальности" in r.text
    # the facts Google's reviewers check are in the Russian version too
    for must in ("Limited Use", "https://policies.google.com/privacy",
                 "https://www.youtube.com/t/terms", "https://myaccount.google.com/permissions"):
        assert must in r.text
    assert 'href="/privacy?lang=en"' in r.text
    r = client.get("/privacy", headers={"accept-language": "en-US,en;q=0.9"})
    assert "<h1>Privacy policy</h1>" in r.text


def test_terms_in_both_languages_link_their_privacy_policy():
    en = client.get("/terms?lang=en").text
    ru = client.get("/terms?lang=ru").text
    assert "<h1>Terms of service</h1>" in en and 'href="/privacy?lang=en"' in en
    assert "<h1>Условия использования</h1>" in ru and 'href="/privacy?lang=ru"' in ru
    # an unknown ?lang= falls back to the request's language
    assert "<h1>Terms of service</h1>" in client.get(
        "/terms?lang=xx", headers={"accept-language": "en"}).text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
