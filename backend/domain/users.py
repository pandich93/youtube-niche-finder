"""Who owns personal data (plan 15). Until multi-user mode is switched on,
every personal row belongs to the one local user. Personal tables carry
`user_id BIGINT NOT NULL DEFAULT 1` and application functions take
`user_id: int = LOCAL_USER_ID` -- there is no global "current user" state.

NF_MULTI_USER=1 turns on sign-in (application/auth.py, the HTTP auth
middleware). Off by default: then everything behaves as a single local user,
with no login, exactly as before.
"""
import os

LOCAL_USER_ID = 1
# Background work nobody asked for in a request (the worker): its LLM spend is
# booked here, not to a person's personal budget. Never a row in users.
SYSTEM_USER_ID = 0

# Tables whose rows belong to one user (plan 15 "personal"). Rows written
# before multi-user mode existed are moved to LOCAL_USER_ID by the migration.
PERSONAL_TABLES = ("tracked_channels", "saved_items", "drafts", "alert_deliveries",
                   "transcript_requests", "llm_usage", "user_topics", "own_channel_daily")


def multi_user_enabled() -> bool:
    return os.environ.get("NF_MULTI_USER", "0").strip().lower() in ("1", "true", "yes")
