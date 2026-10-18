"""Whose YouTube quota a request is spending (plan 15, sub-stage 5.5).

One API key serves the whole installation (YouTube API policies III.D.1.c),
so in multi-user mode each signed-in user gets a daily budget out of it. The
HTTP layer marks the request with its user for the length of that request; the
YouTube client (infrastructure/youtube/client.py) checks and counts against
that budget on every call. The worker and single-user mode set no owner and
spend the shared key as before. Request-scoped on purpose: this is accounting
at the one place every API call passes through, not a "current user" that
application code may read.
"""
from contextvars import ContextVar

_owner = ContextVar("nf_quota_owner", default=None)


def set_owner(user_id):
    return _owner.set(user_id)


def reset(token):
    _owner.reset(token)


def current():
    return _owner.get()


# SaaS sub-project 1: the daily YouTube budget of a user comes from their
# tariff. application/plans.py registers the lookup at import; until then (a
# process that never imported it) the client keeps reading .env.
_limits_provider = None


def set_limits_provider(fn):
    global _limits_provider
    _limits_provider = fn


def limits_for(user_id):
    """{"units": int|None, "searchCalls": int|None} or None when no provider."""
    return _limits_provider(user_id) if _limits_provider else None
