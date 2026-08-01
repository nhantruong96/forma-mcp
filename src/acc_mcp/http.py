"""Shared HTTP layer for APS calls: auth selection, impersonation, errors, retry."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

import httpx

from . import config
from .auth import TOKENS, AuthError

USER_AGENT = "acc-mcp/0.1 (Portcoast CDE assistant)"


class APSError(Exception):
    """API error with an actionable, compact message."""


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:400] or resp.reason_phrase
    if isinstance(data, dict):
        for key in ("detail", "developerMessage", "message", "title", "error_description", "error"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            parts = []
            for e in errors[:3]:
                if isinstance(e, dict):
                    parts.append(str(e.get("detail") or e.get("title") or e))
                else:
                    parts.append(str(e))
            return "; ".join(parts)
    return json.dumps(data)[:400]


def _hint_for(status: int, used_mode: str, url: str) -> str:
    if status == 401:
        return (
            " Hint: token invalid/expired. If using 3-legged, run acc_login; "
            "if 2-legged, check Client ID/Secret."
        )
    if status == 403:
        hints = [
            "the signed-in user may lack permission on this project/folder "
            "(admin rights, or CONTROL for permission changes)",
            "check that the APS app has the needed APIs enabled",
        ]
        if used_mode == "2lo":
            hints.append(
                "2-legged access also requires the app's Client ID as a Custom "
                "Integration in ACC Account Admin of THIS hub"
            )
        return " Hint: " + "; ".join(hints) + "."
    if status == 404 and "/construction/admin/" in url:
        return (
            " Hint: for ACC Admin API a 404 can mean the wrong Region header — "
            "try region='EMEA' (or another region), or verify the account/project id."
        )
    if status == 409:
        return " Hint: conflict — the resource may already exist (e.g. duplicate folder name)."
    if status == 423:
        return " Hint: the folder is temporarily locked by another operation; retry shortly."
    return ""


async def _resolve_token(mode: str) -> tuple[str, str]:
    """Return (access_token, resolved_mode) for mode in auto|3lo|2lo."""
    mode = (mode or config.AUTH_MODE or "auto").lower()
    if mode == "3lo":
        return await TOKENS.get_3lo_token(), "3lo"
    if mode == "2lo":
        return await TOKENS.get_2lo_token(), "2lo"
    # auto: prefer user context
    if TOKENS.has_3lo():
        try:
            return await TOKENS.get_3lo_token(), "3lo"
        except AuthError:
            pass
    return await TOKENS.get_2lo_token(), "2lo"


async def request(
    method: str,
    url: str,
    *,
    auth: str = "auto",
    params: Optional[dict[str, Any]] = None,
    json_body: Any = None,
    content_type: Optional[str] = None,
    region: Optional[str] = None,
    impersonate_header: Optional[str] = None,
    extra_headers: Optional[dict[str, str]] = None,
    expect_json: bool = True,
    max_retries: int = 3,
) -> Any:
    """Perform an APS API call.

    impersonate_header: "x-user-id" (Data Management / BIM 360 Docs) or "User-Id"
    (ACC Admin). Added only in 2-legged context, when a user id is known.
    """
    token, used_mode = await _resolve_token(auth)
    headers: dict[str, str] = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    elif json_body is not None:
        headers["Content-Type"] = "application/json"
    if region:
        headers["Region"] = region
    if impersonate_header and used_mode == "2lo":
        uid = TOKENS.user_id
        if uid:
            headers[impersonate_header] = uid
    if extra_headers:
        headers.update(extra_headers)

    attempt = 0
    while True:
        attempt += 1
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method,
                url,
                params=params,
                content=json.dumps(json_body) if json_body is not None else None,
                headers=headers,
            )
        if resp.status_code in (429, 500, 502, 503, 504) and attempt <= max_retries:
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else min(2 ** attempt, 15)
            except ValueError:
                delay = min(2 ** attempt, 15)
            await asyncio.sleep(delay)
            continue
        break

    if resp.status_code >= 400:
        msg = _extract_error(resp)
        raise APSError(
            f"{method} {resp.request.url.path} -> {resp.status_code} [{used_mode}]: {msg}."
            f"{_hint_for(resp.status_code, used_mode, url)}"
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    if expect_json:
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text[:1000]}
    return resp


def ok(payload: Any = None, **extra: Any) -> str:
    """Uniform JSON success envelope for tool results."""
    out: dict[str, Any] = {}
    if payload is not None:
        out["result"] = payload
    out.update(extra)
    return json.dumps(out, indent=2, ensure_ascii=False, default=str)


def tool_error(exc: Exception) -> str:
    if isinstance(exc, (APSError, AuthError, ValueError)):
        return f"Error: {exc}"
    if isinstance(exc, httpx.TimeoutException):
        return "Error: request timed out — APS may be slow; try again."
    if isinstance(exc, httpx.HTTPError):
        return f"Error: network problem calling APS: {exc}"
    return f"Error: unexpected {type(exc).__name__}: {exc}"
