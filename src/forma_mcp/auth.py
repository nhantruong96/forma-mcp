"""OAuth 2.0 token management for APS: 2-legged + 3-legged (PKCE) with rotation.

Design notes
------------
* 2-legged (client_credentials): cached in memory + token file, refreshed ~60 s early.
* 3-legged (authorization_code + PKCE, confidential client): a temporary local
  HTTP server captures the redirect on the port of APS_CALLBACK_URL. The refresh
  token ROTATES on every use (single-use), so the new one is persisted
  immediately and refreshes are serialized behind an asyncio lock.
* Everything is stored in ~/.forma-mcp/tokens.json (0600 where supported).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional

import httpx

from . import config


class AuthError(Exception):
    """Raised for authentication problems, with actionable guidance."""


def _now() -> float:
    return time.time()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class _CallbackState:
    """Shared state between the login tool and the local callback server."""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.code: Optional[str] = None
        self.state_param: Optional[str] = None
        self.error: Optional[str] = None


class _CallbackHandler(BaseHTTPRequestHandler):
    cb_state: _CallbackState  # injected
    cb_path: str = "/callback"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != self.cb_path:
            self.send_response(404)
            self.end_headers()
            return
        qs = urllib.parse.parse_qs(parsed.query)
        st = self.cb_state
        if "error" in qs:
            st.error = f"{qs.get('error', [''])[0]}: {qs.get('error_description', [''])[0]}"
        st.code = qs.get("code", [None])[0]
        st.state_param = qs.get("state", [None])[0]
        st.event.set()
        body = (
            "<html><body style='font-family:sans-serif;text-align:center;margin-top:80px'>"
            "<h2>&#10003; Đăng nhập Autodesk thành công</h2>"
            "<p>Bạn có thể đóng tab này và quay lại Claude.</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # silence stderr noise
        return


class TokenManager:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._refresh_lock = asyncio.Lock()
        self._2lo_lock = asyncio.Lock()
        self._pending: Optional[dict[str, Any]] = None  # in-flight login (verifier, state, server)
        self._load()

    # ------------------------------------------------------------------ store
    def _load(self) -> None:
        try:
            if config.TOKEN_FILE.is_file():
                self._store = json.loads(config.TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._store = {}

    def _save(self) -> None:
        config.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.TOKEN_FILE.write_text(json.dumps(self._store, indent=2), encoding="utf-8")
        try:
            config.TOKEN_FILE.chmod(0o600)
        except OSError:
            pass

    # ------------------------------------------------------------- validation
    @staticmethod
    def _require_app() -> None:
        if not config.CLIENT_ID:
            raise AuthError(
                "APS_CLIENT_ID is not configured. Set it in the Claude Desktop config "
                "(env block) or in a .env file. APS_CLIENT_SECRET is additionally "
                "required only for a 'Traditional Web App' type APS app. "
                "See HUONG_DAN_CAI_DAT.md for how to create the APS app."
            )

    @staticmethod
    def _client_auth() -> tuple[dict[str, str], dict[str, str]]:
        """Client authentication for the token endpoint.

        Confidential client (secret set): HTTP Basic header.
        Public client (Desktop/Mobile/SPA app, no secret): client_id in the body.
        Never both — APS rejects mixed header+body client auth with 400.
        """
        if config.CLIENT_SECRET:
            basic = base64.b64encode(
                f"{config.CLIENT_ID}:{config.CLIENT_SECRET}".encode()
            ).decode()
            return {"Authorization": f"Basic {basic}"}, {}
        return {}, {"client_id": config.CLIENT_ID}

    # ------------------------------------------------------------- 2-legged
    async def get_2lo_token(self) -> str:
        self._require_app()
        if not config.CLIENT_SECRET:
            raise AuthError(
                "2-legged access needs a client secret (Traditional Web App). This "
                "server is running 3-legged-only (public client) — run acc_login to "
                "sign in instead."
            )
        tok = self._store.get("two_legged") or {}
        if tok.get("access_token") and tok.get("expires_at", 0) > _now() + 60:
            return tok["access_token"]
        async with self._2lo_lock:
            tok = self._store.get("two_legged") or {}
            if tok.get("access_token") and tok.get("expires_at", 0) > _now() + 60:
                return tok["access_token"]
            data = await self._token_request(
                {"grant_type": "client_credentials", "scope": config.SCOPES_2LO}
            )
            self._store["two_legged"] = {
                "access_token": data["access_token"],
                "expires_at": _now() + int(data.get("expires_in", 3600)),
            }
            self._save()
            return data["access_token"]

    # ------------------------------------------------------------- 3-legged
    def login_start(self, force_login: bool = False) -> str:
        """Start the browser login flow. Returns the authorization URL."""
        self._require_app()
        self.login_cancel()  # drop any previous pending attempt

        parsed = urllib.parse.urlparse(config.CALLBACK_URL)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        verifier = _b64url(secrets.token_bytes(48))  # 64 chars
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = _b64url(secrets.token_bytes(24))

        cb = _CallbackState()
        handler = type("Handler", (_CallbackHandler,), {"cb_state": cb, "cb_path": parsed.path or "/"})
        try:
            server = HTTPServer(("127.0.0.1", port), handler)
        except OSError as exc:
            raise AuthError(
                f"Cannot open local port {port} for the OAuth callback ({exc}). "
                f"Close the application using that port, or change APS_CALLBACK_URL "
                f"(remember to register the same URL in the APS app)."
            ) from exc
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        params = {
            "response_type": "code",
            "client_id": config.CLIENT_ID,
            "redirect_uri": config.CALLBACK_URL,
            "scope": config.SCOPES_3LO,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if force_login:
            params["prompt"] = "login"
        url = f"{config.AUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        self._pending = {
            "cb": cb,
            "server": server,
            "verifier": verifier,
            "state": state,
            "url": url,
            "started": _now(),
        }

        # Open the browser in a daemon thread. webbrowser.open() can block for
        # many seconds (cold browser launch, shell handler); doing it inline
        # would eat the caller's whole time budget. The user can always open the
        # returned URL manually if the auto-open does nothing.
        def _open() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()
        return url

    def login_cancel(self) -> None:
        pending, self._pending = self._pending, None
        if pending:
            try:
                pending["server"].shutdown()
                pending["server"].server_close()
            except Exception:
                pass

    async def login_wait(self, timeout: float = 120.0) -> dict[str, Any]:
        """Wait for the browser redirect, then exchange the code for tokens."""
        pending = self._pending
        if not pending:
            raise AuthError("No login in progress. Call acc_login first.")
        cb: _CallbackState = pending["cb"]
        deadline = _now() + timeout
        while not cb.event.is_set():
            if _now() > deadline:
                raise AuthError(
                    "Timed out waiting for the browser login. The login page is still open — "
                    "finish signing in, then call acc_login again with wait_only=true."
                )
            await asyncio.sleep(0.5)

        try:
            if cb.error:
                raise AuthError(f"Autodesk returned an error: {cb.error}")
            if cb.state_param != pending["state"]:
                raise AuthError("State mismatch in OAuth callback (possible CSRF). Try again.")
            if not cb.code:
                raise AuthError("No authorization code received. Try acc_login again.")
            data = await self._token_request(
                {
                    "grant_type": "authorization_code",
                    "code": cb.code,
                    "redirect_uri": config.CALLBACK_URL,
                    "code_verifier": pending["verifier"],
                }
            )
        finally:
            self.login_cancel()

        self._store_3lo(data)
        user = await self._fetch_userinfo(data["access_token"])
        self._store["three_legged"]["user"] = user
        self._save()
        return user

    def _store_3lo(self, data: dict[str, Any]) -> None:
        prev = self._store.get("three_legged") or {}
        self._store["three_legged"] = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token") or prev.get("refresh_token"),
            "expires_at": _now() + int(data.get("expires_in", 3600)),
            "obtained_at": _now(),
            "user": prev.get("user"),
        }
        self._save()

    async def _fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                config.USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
        if resp.status_code != 200:
            return {}
        info = resp.json()
        return {
            "autodeskId": info.get("sub"),
            "name": info.get("name"),
            "email": info.get("email"),
        }

    async def get_3lo_token(self) -> str:
        tok = self._store.get("three_legged") or {}
        if not tok.get("refresh_token") and not tok.get("access_token"):
            raise AuthError("Not logged in. Call acc_login to sign in with your Autodesk account.")
        if tok.get("access_token") and tok.get("expires_at", 0) > _now() + 60:
            return tok["access_token"]
        async with self._refresh_lock:
            tok = self._store.get("three_legged") or {}
            if tok.get("access_token") and tok.get("expires_at", 0) > _now() + 60:
                return tok["access_token"]
            if not tok.get("refresh_token"):
                raise AuthError("Session expired and no refresh token stored. Call acc_login.")
            try:
                data = await self._token_request(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": tok["refresh_token"],
                        "scope": config.SCOPES_3LO,
                    }
                )
            except AuthError as exc:
                if "invalid_grant" in str(exc).lower():
                    self._store.pop("three_legged", None)
                    self._save()
                    raise AuthError(
                        "The refresh token is no longer valid (expired after ~15 days of no use, "
                        "or used concurrently). Call acc_login to sign in again."
                    ) from exc
                raise
            self._store_3lo(data)
            return data["access_token"]

    # ------------------------------------------------------------- shared
    async def _token_request(self, body: dict[str, str]) -> dict[str, Any]:
        auth_headers, auth_body = self._client_auth()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                config.AUTH_TOKEN_URL,
                data={**body, **auth_body},
                headers={
                    **auth_headers,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        if resp.status_code != 200:
            try:
                err = resp.json()
                detail = err.get("error_description") or err.get("error") or resp.text[:300]
            except ValueError:
                detail = resp.text[:300]
            hint = ""
            if resp.status_code in (400, 401):
                hint = (
                    " Check APS_CLIENT_ID (and APS_CLIENT_SECRET if set — a Traditional Web App "
                    "REQUIRES the secret; a Desktop/Mobile/SPA app must leave it EMPTY), and note "
                    "that since Dec 2025 the APS app must belong to a Developer Hub "
                    "(see HUONG_DAN_CAI_DAT.md §1)."
                )
            raise AuthError(f"Token request failed ({resp.status_code}): {detail}.{hint}")
        return resp.json()

    # ------------------------------------------------------------- accessors
    def logout(self) -> None:
        self.login_cancel()
        self._store.pop("three_legged", None)
        self._save()

    @property
    def user(self) -> Optional[dict[str, Any]]:
        return (self._store.get("three_legged") or {}).get("user")

    @property
    def user_id(self) -> Optional[str]:
        """Autodesk user id for impersonation headers (3LO user or ACC_USER_ID env)."""
        u = self.user or {}
        return u.get("autodeskId") or (config.FALLBACK_USER_ID or None)

    def has_3lo(self) -> bool:
        tok = self._store.get("three_legged") or {}
        return bool(tok.get("refresh_token") or tok.get("access_token"))

    def status(self) -> dict[str, Any]:
        t3 = self._store.get("three_legged") or {}
        t2 = self._store.get("two_legged") or {}
        return {
            "app_configured": bool(config.CLIENT_ID),
            "client_type": "confidential (secret set)" if config.CLIENT_SECRET else "public (PKCE, no secret)",
            "auth_mode": config.AUTH_MODE,
            "region_default": config.DEFAULT_REGION,
            "three_legged": {
                "logged_in": self.has_3lo(),
                "user": t3.get("user"),
                "access_token_valid_for_s": max(0, int(t3.get("expires_at", 0) - _now())) or None,
            },
            "two_legged": {
                "token_cached": bool(t2.get("access_token")),
                "valid_for_s": max(0, int(t2.get("expires_at", 0) - _now())) or None,
            },
            "login_pending": bool(self._pending),
            "callback_url": config.CALLBACK_URL,
            "token_file": str(config.TOKEN_FILE),
        }


TOKENS = TokenManager()
