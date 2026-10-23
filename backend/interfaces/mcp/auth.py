"""MCP over HTTP with sign-in (plan 15, sub-stage 5.8).

With NF_MULTI_USER=1 and an HTTP transport, the MCP server accepts only
"Authorization: Bearer nf_..." -- the same personal API tokens as the browser
extension (dashboard: create one while signed in; or `cli.py create-token`).
Tools then act as the token's user: their own watchlist, drafts, alerts and
own channels, and YouTube calls count against that user's daily budget.
stdio stays local and acts as user 1, exactly as before.
"""
import os

from anyio import to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings

from domain.users import LOCAL_USER_ID, multi_user_enabled
from infrastructure import quota_owner

SCOPE = "niche-finder"


def http_auth_enabled() -> bool:
    return multi_user_enabled() and os.environ.get("MCP_TRANSPORT", "stdio") != "stdio"


class ApiTokenVerifier:
    """TokenVerifier for the SDK: a personal API token -> an AccessToken whose
    client_id is the user id."""

    async def verify_token(self, token: str):
        from application import auth
        user = await to_thread.run_sync(auth.user_for_api_token, token)
        if not user:
            return None
        return AccessToken(token=token, client_id=str(user["id"]), scopes=[SCOPE],
                           subject=str(user["id"]))


def auth_settings() -> AuthSettings:
    base = os.environ.get("MCP_PUBLIC_URL", "").strip() or "https://localhost:8765"
    return AuthSettings(issuer_url=base, resource_server_url=base.rstrip("/") + "/mcp",
                        required_scopes=[SCOPE])


def current_user_id() -> int:
    """The token's user on HTTP, the local user on stdio (no token)."""
    tok = get_access_token()
    if tok and tok.client_id and tok.client_id.isdigit():
        return int(tok.client_id)
    return LOCAL_USER_ID


# SaaS sub-project 1: MCP tools that need a tariff feature beyond api_access
MCP_TOOL_FEATURES = {"own_channels": "own_channels", "own_vs_niche": "own_channels",
                     "own_channel_formats": "own_channels", "sync_own_channels": "own_channels",
                     "rpm_calibration": ("own_channels", "own_revenue")}


def blocked_feature(user_id: int, method, params):
    """The tariff feature a token's user lacks for this MCP message, else None."""
    from application import plans
    features = plans.plan_for(user_id).features
    if not features["api_access"]:
        return "api_access"
    if method == "tools/call":
        needed = MCP_TOOL_FEATURES.get((params or {}).get("name")) or ()
        for feature in ((needed,) if isinstance(needed, str) else needed):
            if not features[feature]:
                return feature
    return None


def _saved_locale(user_id: int):
    from application import locale_prefs
    try:
        return locale_prefs.get(user_id=user_id)
    except Exception:
        return None


async def locale_middleware(ctx, call_next):
    """Scenario texts and tool hints in the language the user chose in the
    dashboard (i18n plan): the token's user over HTTP, the local user over
    stdio. Nothing saved -> NF_DEFAULT_LOCALE. Runs outermost, so a tariff
    refusal from quota_owner_middleware is in that language too."""
    from domain import i18n
    saved = await to_thread.run_sync(_saved_locale, current_user_id())
    if not saved:
        return await call_next(ctx)
    with i18n.use_locale(saved):
        return await call_next(ctx)


async def quota_owner_middleware(ctx, call_next):
    """YouTube calls made by a tool count against the token's user (5.5); a
    tool the user's tariff does not include is refused before it runs."""
    tok = get_access_token()
    if not (tok and tok.client_id and tok.client_id.isdigit()):
        return await call_next(ctx)
    uid = int(tok.client_id)
    feature = await to_thread.run_sync(blocked_feature, uid, getattr(ctx, "method", None),
                                       getattr(ctx, "params", None))
    if feature:
        from domain.plans import FeatureOff
        raise FeatureOff(feature)
    reset = quota_owner.set_owner(uid)
    try:
        return await call_next(ctx)
    finally:
        quota_owner.reset(reset)
