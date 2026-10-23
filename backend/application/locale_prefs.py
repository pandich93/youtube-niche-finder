"""The interface language a person chose (i18n plan): read by the dashboard on
start (another device, another browser) and by the worker, which writes each
user's alerts and digest in their language. One row per user in user_prefs;
single-user installs keep theirs under the local user like everything else.
Nothing saved -> NF_DEFAULT_LOCALE.
"""
import infrastructure.postgres as db
from domain import i18n


class LocaleError(ValueError):
    pass


def get(*, user_id: int):
    """The saved locale, or None when the person never chose one."""
    conn = db.get_conn()
    try:
        r = conn.execute("SELECT locale FROM user_prefs WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return i18n.normalize(r["locale"]) if r and r["locale"] else None


def effective(*, user_id: int) -> str:
    """The language to write to this user in when no request says otherwise."""
    return get(user_id=user_id) or i18n.default_locale()


def save(locale, *, user_id: int) -> str:
    loc = i18n.normalize(locale)
    if loc is None:
        raise LocaleError(f"locale must be one of {', '.join(i18n.SUPPORTED)}")
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO user_prefs (user_id, locale, updated_at) VALUES (?,?,?) "
            "ON CONFLICT (user_id) DO UPDATE SET locale = EXCLUDED.locale, "
            "updated_at = EXCLUDED.updated_at", (user_id, loc, db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    return loc
