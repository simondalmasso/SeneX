from __future__ import annotations

import os

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from .github_policy import (
    apply_protection,
    get_protection,
    protection_summary,
    require_authorized_user,
)

PUBLIC_BASE_URL = (os.environ.get("SENEX_ADMIN_MCP_BASE_URL") or "").rstrip("/")
if not PUBLIC_BASE_URL.startswith("https://"):
    raise RuntimeError("SENEX_ADMIN_MCP_BASE_URL must be an https:// URL")
RESOURCE_URL = f"{PUBLIC_BASE_URL}/mcp"


class GitHubUserTokenVerifier(TokenVerifier):
    """Accept only a GitHub App user token for the SENEX owner."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            identity = require_authorized_user(token)
        except Exception:
            return None
        return AccessToken(
            token=token,
            client_id=f"github:{identity['login']}",
            subject=str(identity.get("id") or identity["login"]),
            scopes=["senex:admin"],
        )


mcp = MCPServer(
    "SENEX GitHub Admin",
    token_verifier=GitHubUserTokenVerifier(),
    auth=AuthSettings(
        # The bearer presented to this resource is a GitHub App user-to-server
        # access token obtained by the caller through GitHub OAuth/SSO.
        issuer_url=AnyHttpUrl("https://github.com"),
        resource_server_url=AnyHttpUrl(RESOURCE_URL),
        required_scopes=["senex:admin"],
        validate_token_resource=False,
    ),
)


def _caller_token() -> str:
    auth = get_access_token()
    if auth is None or not auth.token:
        raise PermissionError("SENEX_ADMIN_AUTH_REQUIRED")
    return auth.token


@mcp.tool()
def verify_main_protection() -> dict:
    """Read the protection state of simondalmasso/SeneX main. No mutation."""
    token = _caller_token()
    return protection_summary(get_protection(token))


@mcp.tool()
def protect_main(confirm: str) -> dict:
    """Protect only SENEX main. confirm must equal PROTECT_SENEX_MAIN."""
    if confirm != "PROTECT_SENEX_MAIN":
        raise ValueError("CONFIRM_LITERAL_REQUIRED")

    token = _caller_token()
    applied = apply_protection(token)
    summary = protection_summary(applied)
    if not summary["pass"]:
        raise RuntimeError(f"PROTECTION_POSTCONDITION_FAILED: {summary}")
    return summary


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        streamable_http_path="/mcp",
        json_response=True,
    )
