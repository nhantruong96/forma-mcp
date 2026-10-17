"""Shared helpers used by multiple tool modules."""

from __future__ import annotations

from typing import Any, Optional

from .. import config
from ..http import APSError, request
from ..ids import enc, with_b, without_b

# Regions to probe for ACC Admin API routing, most likely first
_PROBE_REGIONS = ["US", "EMEA", "AUS", "CAN", "DEU", "GBR", "IND", "JPN"]
_region_cache: dict[str, str] = {}


async def admin_region(account_id: str) -> str:
    """Resolve (and cache) the Region header value for an account."""
    acct = without_b(account_id)
    if acct in _region_cache:
        return _region_cache[acct]
    ordered = [config.DEFAULT_REGION] + [r for r in _PROBE_REGIONS if r != config.DEFAULT_REGION]
    last_exc: Optional[Exception] = None
    for reg in ordered:
        try:
            await request(
                "GET",
                f"{config.ADMIN_V1}/accounts/{acct}/projects",
                params={"limit": 1},
                region=reg,
                impersonate_header="User-Id",
            )
            _region_cache[acct] = reg
            return reg
        except APSError as exc:
            last_exc = exc
            if "-> 404" in str(exc):
                continue
            raise
    raise APSError(
        f"Could not reach ACC Admin API for account {acct} in any region "
        f"({', '.join(ordered)}). Last error: {last_exc}"
    )


def set_region_hint(account_id: str, region: Optional[str]) -> None:
    if region:
        _region_cache[without_b(account_id)] = region.upper()


# ---------------------------------------------------------------- formatting

def fmt_project_admin(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "dmId": with_b(p.get("id", "")),
        "name": p.get("name"),
        "platform": p.get("platform"),
        "status": p.get("status"),
        "classification": p.get("classification"),
        "type": p.get("type"),
        "jobNumber": p.get("jobNumber"),
        "startDate": p.get("startDate"),
        "endDate": p.get("endDate"),
        "memberCount": p.get("memberCount"),
        "templateId": p.get("templateId"),
    }


def fmt_products(p: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": pr.get("key"), "status": pr.get("status")}
        for pr in (p.get("products") or [])
    ]


def fmt_folder(d: dict[str, Any]) -> dict[str, Any]:
    attrs = d.get("attributes", {})
    ext = (attrs.get("extension") or {}).get("data") or {}
    out = {
        "type": "folder",
        "id": d.get("id"),
        "name": attrs.get("displayName") or attrs.get("name"),
        "objectCount": attrs.get("objectCount"),
        "hidden": attrs.get("hidden"),
    }
    if ext.get("namingStandardIds"):
        out["namingStandardIds"] = ext["namingStandardIds"]
    return out


def fmt_item(d: dict[str, Any], included_versions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    attrs = d.get("attributes", {})
    out: dict[str, Any] = {
        "type": "file",
        "id": d.get("id"),
        "name": attrs.get("displayName"),
        "hidden": attrs.get("hidden"),
    }
    tip_id = (((d.get("relationships") or {}).get("tip") or {}).get("data") or {}).get("id")
    if tip_id:
        out["tipVersionId"] = tip_id
        if included_versions and tip_id in included_versions:
            out.update(fmt_version_brief(included_versions[tip_id]))
    return out


def fmt_version_brief(v: dict[str, Any]) -> dict[str, Any]:
    attrs = v.get("attributes", {})
    ext = (attrs.get("extension") or {}).get("data") or {}
    out = {
        "versionNumber": attrs.get("versionNumber"),
        "fileType": attrs.get("fileType"),
        "storageSize": attrs.get("storageSize"),
        "lastModifiedTime": attrs.get("lastModifiedTime"),
        "lastModifiedUserName": attrs.get("lastModifiedUserName"),
    }
    for key in ("processState", "reviewState", "conformingStatus", "revisionDisplayLabel"):
        if ext.get(key):
            out[key] = ext[key]
    return out


def fmt_version(v: dict[str, Any]) -> dict[str, Any]:
    out = fmt_version_brief(v)
    out["id"] = v.get("id")
    attrs = v.get("attributes", {})
    out["name"] = attrs.get("displayName") or attrs.get("name")
    storage = (((v.get("relationships") or {}).get("storage") or {}).get("data") or {}).get("id")
    if storage:
        out["storageUrn"] = storage
    return out


def included_versions_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        inc["id"]: inc
        for inc in payload.get("included", [])
        if inc.get("type") == "versions"
    }


def dm_page_info(payload: dict[str, Any]) -> dict[str, Any]:
    links = payload.get("links") or {}
    return {"hasMore": bool((links.get("next") or {}).get("href"))}


# ---------------------------------------------------------------- DM listing

async def list_folder_contents(
    project_id: str,
    folder_id: str,
    *,
    page_number: int = 0,
    page_limit: int = 50,
    filter_type: Optional[str] = None,
    include_hidden: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {"page[number]": page_number, "page[limit]": page_limit}
    if filter_type:
        params["filter[type]"] = filter_type
    if include_hidden:
        params["includeHidden"] = "true"
    payload = await request(
        "GET",
        f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}/contents",
        params=params,
        impersonate_header="x-user-id",
    )
    vmap = included_versions_map(payload)
    entries = []
    for d in payload.get("data", []):
        if d.get("type") == "folders":
            entries.append(fmt_folder(d))
        elif d.get("type") == "items":
            entries.append(fmt_item(d, vmap))
    return {"entries": entries, **dm_page_info(payload)}


async def find_item_in_folder(project_id: str, folder_id: str, filename: str) -> Optional[str]:
    """Return the item (lineage) URN of a file with this display name, if present."""
    payload = await request(
        "GET",
        f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}/search",
        params={"filter[attributes.displayName]": filename, "page[number]": 0},
        impersonate_header="x-user-id",
    )
    for inc in payload.get("included", []):
        if inc.get("type") == "items":
            return inc.get("id")
    # fallback: some responses carry versions in data with item relationship
    for d in payload.get("data", []):
        item = (((d.get("relationships") or {}).get("item") or {}).get("data") or {}).get("id")
        if item:
            return item
    return None


# ---------------------------------------------------------------- users

async def find_project_user(project_id: str, email: str) -> Optional[dict[str, Any]]:
    payload = await request(
        "GET",
        f"{config.ADMIN_V1}/projects/{without_b(project_id)}/users",
        params={"filter[email]": email, "limit": 5},
    )
    for u in payload.get("results", []):
        if (u.get("email") or "").lower() == email.lower():
            return u
    results = payload.get("results", [])
    return results[0] if results else None


def parse_products_spec(spec: Optional[str]) -> Optional[list[dict[str, str]]]:
    """Parse 'docs:member,projectAdministration:administrator' into API form."""
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"Invalid products spec '{part}'. Use key:access pairs, e.g. "
                "'docs:member' or 'projectAdministration:administrator'."
            )
        key, _, access = part.partition(":")
        out.append({"key": key.strip(), "access": access.strip()})
    return out
