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


async def quota_owner_middleware(ctx, call_next):
    """YouTube calls made by a tool count against the token's user (5.5)."""
    tok = get_access_token()
    if not (tok and tok.client_id and tok.client_id.isdigit()):
        return await call_next(ctx)
    reset = quota_owner.set_owner(int(tok.client_id))
    try:
        return await call_next(ctx)
    finally:
        quota_owner.reset(reset)
