"""Authentication tools: status, login (3-legged), logout, whoami."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..app import mcp
from ..auth import TOKENS
from ..http import ok, request, tool_error
from .. import config


@mcp.tool(
    name="acc_auth_status",
    annotations={"title": "ACC: Auth status", "readOnlyHint": True},
)
async def acc_auth_status() -> str:
    """Check the authentication state of the ACC connection.

    Shows whether the APS app credentials are configured, whether a 3-legged
    user session exists (and for whom), 2-legged token cache state, the default
    region and the callback URL. Call this first in a session, and whenever an
    auth error occurs.
    """
    try:
        return ok(TOKENS.status())
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_login",
    annotations={"title": "ACC: Sign in (3-legged)", "readOnlyHint": False, "destructiveHint": False},
)
async def acc_login(
    wait_seconds: Annotated[int, Field(ge=5, le=290, description="How long to wait for the browser sign-in before giving up. Internally capped per call to stay under the MCP host's request timeout; poll again to keep waiting")] = 45,
    wait_only: Annotated[bool, Field(description="True = do not start a new flow, just keep waiting for a login already in progress")] = False,
    force_login: Annotated[bool, Field(description="Force the Autodesk login page even if a browser session exists (prompt=login)")] = False,
) -> str:
    """Sign in with an Autodesk account (3-legged OAuth with PKCE).

    Opens the Autodesk login page in the default browser (non-blocking) and
    ALWAYS returns the authorization URL so the user can open it manually, then
    waits for the redirect on the local callback port, exchanges the code and
    stores the rotating refresh token. The wait is capped per call (~45 s) to
    stay under the MCP host's request timeout — if it returns "still waiting",
    the login page stays open and the callback keeps listening, so just call
    acc_login again with wait_only=true to keep polling until the user finishes.
    After ~15 days without use the session expires and this tool must be re-run.
    """
    # Cap each call safely below typical 60 s MCP-host request timeouts.
    effective = min(int(wait_seconds), 45)
    try:
        url = None
        if not wait_only:
            url = TOKENS.login_start(force_login=force_login)
        try:
            user = await TOKENS.login_wait(timeout=float(effective))
            return ok({"loggedInAs": user, "message": "3-legged session active."})
        except Exception as exc:
            msg = str(exc)
            if "Timed out" in msg or "still" in msg.lower():
                out = (
                    "Still waiting for the browser sign-in — not finished yet. "
                    "The login page is open and the callback is still listening; "
                    "ask the user to sign in and click Allow, then call acc_login "
                    "again with wait_only=true."
                )
                if url:
                    out += f"\nIf no browser opened, open this URL manually:\n{url}"
                return out
            if url:
                return (
                    f"Browser sign-in started but errored: {exc}\n"
                    f"If no browser opened, open this URL manually:\n{url}\n"
                    f"Then call acc_login with wait_only=true."
                )
            raise
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_logout",
    annotations={"title": "ACC: Sign out", "readOnlyHint": False},
)
async def acc_logout() -> str:
    """Discard the stored 3-legged session (refresh token). Use before
    switching Autodesk accounts; sign in again with acc_login."""
    try:
        TOKENS.logout()
        return ok({"message": "3-legged session cleared."})
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_whoami",
    annotations={"title": "ACC: Who am I", "readOnlyHint": True},
)
async def acc_whoami() -> str:
    """Show the signed-in Autodesk user (3-legged) and the ACC hubs the current
    credentials can see. Useful to verify access after login and to fetch hub
    (account) ids for the admin tools."""
    try:
        out: dict = {"user": TOKENS.user}
        payload = await request("GET", f"{config.PROJECT_V1}/hubs", impersonate_header="x-user-id")
        hubs = []
        for h in payload.get("data", []):
            attrs = h.get("attributes", {})
            hubs.append(
                {
                    "hubId": h.get("id"),
                    "accountId": h.get("id", "").removeprefix("b."),
                    "name": attrs.get("name"),
                    "region": ((attrs.get("extension") or {}).get("data") or {}).get("region"),
                }
            )
        out["hubs"] = hubs
        if not hubs:
            out["note"] = (
                "No hubs visible. The signed-in user must be a member of at least one "
                "ACC hub (any role). If a hub is missing, ask its admin to add this "
                "user; if not signed in, run acc_login."
            )
        return ok(out)
    except Exception as exc:
        return tool_error(exc)
