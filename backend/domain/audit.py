"""What the action log keeps (multi-user plan P1-07): the list of events and
the rules for their details. Pure functions, no database.

An entry is: when, who did it (actor_id), to whom (target_id), what (event),
from which address, and a small JSON of details. Details never hold a
password, a token, a secret or a hash -- clean_details() drops such keys at
any depth, whatever the caller passed, and cuts long strings, so a slip in a
caller cannot put one into the log.

actor_id: 0 = the operator (CLI, the worker); NULL = nobody signed in (a
failed sign-in). The log keeps no user_id column: it outlives a deleted
account as a security record and is cleared by age (retention_days()).
"""
import os
import re

OPERATOR_ID = 0

# event -> the group the admin screen filters by
EVENTS = {
    "login": "auth",
    "login_failed": "auth",
    "signup": "auth",
    "password_change": "auth",
    "password_reset": "auth",
    "email_change_requested": "auth",
    "email_change": "auth",
    # multi-user plan P2-04: второй фактор
    "2fa_enable": "auth",
    "2fa_disable": "auth",
    "2fa_failed": "auth",
    "2fa_backup_used": "auth",
    "2fa_backup_regenerate": "auth",
    "token_create": "tokens",
    "token_revoke": "tokens",
    "plan_change": "plans",
    "account_create": "accounts",
    "account_disable": "accounts",
    "account_enable": "accounts",
    "account_delete": "accounts",
    "account_self_delete": "accounts",
    "plan_create": "admin",
    "plan_update": "admin",
    "plan_archive": "admin",
    "plan_restore": "admin",
}

# admin_actions (P0-04) -> the event it becomes in audit_log
LEGACY_ACTIONS = {"create": "account_create", "disable": "account_disable",
                  "enable": "account_enable", "delete": "account_delete",
                  "self-delete": "account_self_delete"}

# a key that looks like it may carry a credential is never stored
_SECRET_KEY = re.compile(r"pass(word)?|token|secret|hash|key|cookie|auth", re.I)
_MAX_STR = 200
_MAX_DEPTH = 3

RETENTION_DEFAULT = 180


def clean_details(details, _depth: int = 0):
    """A copy of details safe to store: no credential-like keys, short
    strings, plain JSON types only."""
    if details is None:
        return {}
    if isinstance(details, dict):
        if _depth >= _MAX_DEPTH:
            return {}
        return {str(k)[:60]: clean_details(v, _depth + 1) for k, v in details.items()
                if not _SECRET_KEY.search(str(k))}
    if isinstance(details, (list, tuple)):
        return [clean_details(v, _depth + 1) for v in list(details)[:20]]
    if isinstance(details, bool) or details is None or isinstance(details, (int, float)):
        return details
    return str(details)[:_MAX_STR]


def retention_days() -> int:
    """NF_AUDIT_RETENTION_DAYS: how long an entry is kept; 0 = forever."""
    try:
        return max(0, int(os.environ.get("NF_AUDIT_RETENTION_DAYS") or RETENTION_DEFAULT))
    except ValueError:
        return RETENTION_DEFAULT
