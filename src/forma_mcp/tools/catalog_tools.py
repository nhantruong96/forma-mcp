"""Tools: Autodesk Content Catalog (company content library) — browse and download.

Read and download only. The catalog API also has update/delete endpoints; they are
deliberately not exposed here.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any, Optional, Union

import httpx
from pydantic import Field

from ..app import mcp
from ..http import APSError, ok, request, tool_error
from ..ids import without_b
from .. import config

# Content list per hub. A company library can be tens of thousands of items and the
# API has no name search, so the list is read page by page across calls (each call
# stops before the MCP host's request timeout) and saved to disk once complete.
_LIST_BUDGET_S = 30
_state: dict[str, dict[str, Any]] = {}  # hub -> {"items": [...], "cursor": str|None, "done": bool}


def _cache_file(hub: str) -> Path:
    return config.DATA_DIR / "catalog" / f"{hub}.json"


def _hub(hub_id: str) -> str:
    """Catalog hub id = the hub GUID without 'b.', upper case."""
    return without_b(hub_id).upper()


def _base(hub_id: str) -> str:
    return f"{config.CATALOG_V2}/hubs/{_hub(hub_id)}"


def _fmt_content(c: dict[str, Any]) -> dict[str, Any]:
    rev = c.get("currentRevision") or {}
    return {
        "id": c.get("id"),
        "fileName": c.get("fileName"),
        "contentType": c.get("contentType"),
        "product": c.get("product"),
        "category": rev.get("category"),
        "productVersions": rev.get("productVersions"),
        "revisionId": rev.get("id"),
        "revisionNumber": rev.get("revisionNumber"),
        "collectionIds": c.get("collectionIds"),
    }


async def _read_contents(hub_id: str, region: Optional[str], refresh: bool) -> dict[str, Any]:
    """Advance the hub's content list for up to _LIST_BUDGET_S seconds; return its state."""
    key = _hub(hub_id)
    st = _state.get(key)
    if st is None or refresh:
        f = _cache_file(key)
        if f.is_file() and not refresh:
            st = {"items": json.loads(f.read_text(encoding="utf-8")), "cursor": None, "done": True}
        else:
            st = {"items": [], "cursor": None, "done": False}
        _state[key] = st
    start = time.monotonic()
    while not st["done"] and time.monotonic() - start < _LIST_BUDGET_S:
        params: dict[str, Any] = {"limit": 1000}
        if st["cursor"]:
            params["cursorState"] = st["cursor"]
        for attempt in range(4):
            try:
                page = await request("GET", f"{_base(hub_id)}/contents", params=params, auth="3lo", region=region)
                break
            except httpx.TransportError:  # the catalog drops long listings now and then
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)
        st["items"].extend(page.get("results") or [])
        st["cursor"] = (page.get("pagination") or {}).get("nextCursorState")
        if not st["cursor"]:
            st["done"] = True
            f = _cache_file(key)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(st["items"], ensure_ascii=False), encoding="utf-8")
    return st


@mcp.tool(
    name="catalog_get_hub",
    annotations={"title": "Content Catalog: Get hub", "readOnlyHint": True},
)
async def catalog_get_hub(
    hub_id: Annotated[str, Field(description="Hub id (with or without 'b.')")],
    region: Annotated[Optional[str], Field(description="Region header (default US)")] = None,
) -> str:
    """Content Catalog settings of a hub: whether the catalog is enabled and the
    signed-in user's role (admin, collection access). Requires 3-legged login."""
    try:
        return ok(await request("GET", _base(hub_id), auth="3lo", region=region))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="catalog_list_collections",
    annotations={"title": "Content Catalog: List collections", "readOnlyHint": True},
)
async def catalog_list_collections(
    hub_id: Annotated[str, Field(description="Hub id (with or without 'b.')")],
    region: Annotated[Optional[str], Field(description="Region header (default US)")] = None,
) -> str:
    """List the collections (library folders) of the hub's Content Catalog."""
    try:
        out: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": 100}
        while True:
            page = await request("GET", f"{_base(hub_id)}/collections", params=params, auth="3lo", region=region)
            out.extend({"id": c.get("id"), "name": c.get("name")} for c in page.get("results") or [])
            cursor = (page.get("pagination") or {}).get("nextCursorState")
            if not cursor:
                break
            params = {"limit": 100, "cursorState": cursor}
        return ok(out, count=len(out))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="catalog_search_content",
    annotations={"title": "Content Catalog: Search content", "readOnlyHint": True},
)
async def catalog_search_content(
    hub_id: Annotated[str, Field(description="Hub id (with or without 'b.')")],
    name_contains: Annotated[Optional[str], Field(description="Case-insensitive substring of the file name; several words = all must match")] = None,
    content_type: Annotated[Optional[str], Field(description="RFA, RVT, DWG, ... (case-insensitive)")] = None,
    collection_id: Annotated[Optional[str], Field(description="Only items in this collection")] = None,
    max_results: Annotated[int, Field(description="Maximum items returned", ge=1, le=500)] = 50,
    refresh: Annotated[bool, Field(description="Re-read the whole library instead of using the saved list")] = False,
    region: Annotated[Optional[str], Field(description="Region header (default US)")] = None,
) -> str:
    """Find items in the Content Catalog by file name, type or collection. The API
    has no name search, so the library list is read once (1 000 items per page,
    about 30 s per call) and saved under the data dir; while "complete" is false,
    call again with the same arguments to continue reading."""
    try:
        st = await _read_contents(hub_id, region, refresh)
        items = st["items"]
        words = (name_contains or "").lower().split()
        hits = [
            c for c in items
            if all(w in (c.get("fileName") or "").lower() for w in words)
            and (not content_type or (c.get("contentType") or "").upper() == content_type.upper())
            and (not collection_id or collection_id in (c.get("collectionIds") or []))
        ]
        return ok([_fmt_content(c) for c in hits[:max_results]], matched=len(hits),
                  itemsRead=len(items), complete=st["done"])
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="catalog_get_content",
    annotations={"title": "Content Catalog: Get content", "readOnlyHint": True},
)
async def catalog_get_content(
    hub_id: Annotated[str, Field(description="Hub id (with or without 'b.')")],
    content_id: Annotated[str, Field(description="Content id (from catalog_search_content)")],
    region: Annotated[Optional[str], Field(description="Region header (default US)")] = None,
) -> str:
    """Full metadata of one catalog item (category, product versions, type
    parameters) and the downloadable representations of its current revision."""
    try:
        c = await request("GET", f"{_base(hub_id)}/contents/{content_id}", auth="3lo", region=region)
        rev_id = (c.get("currentRevision") or {}).get("id")
        reps = []
        if rev_id:
            r = await request("GET", f"{_base(hub_id)}/contents/{content_id}/revisions/{rev_id}/representations",
                              auth="3lo", region=region)
            reps = r.get("results") or []
        return ok(c, representations=reps)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="catalog_download_content",
    annotations={"title": "Content Catalog: Download content", "readOnlyHint": False},
)
async def catalog_download_content(
    hub_id: Annotated[str, Field(description="Hub id (with or without 'b.')")],
    content_id: Annotated[str, Field(description="Content id (from catalog_search_content)")],
    product_version: Annotated[Optional[Union[str, int]], Field(description="Product version of the representation, e.g. '2027' (default: newest)")] = None,
    dest_path: Annotated[Optional[str], Field(description="Destination file or folder on this computer (default: <data dir>/downloads)")] = None,
    region: Annotated[Optional[str], Field(description="Region header (default US)")] = None,
) -> str:
    """Download the file of a catalog item (current revision) to the local disk.
    The pre-signed URL request needs the data:write scope; nothing on the catalog
    is changed."""
    try:
        c = await request("GET", f"{_base(hub_id)}/contents/{content_id}", auth="3lo", region=region)
        rev_id = (c.get("currentRevision") or {}).get("id")
        if not rev_id:
            raise APSError("Content has no current revision.")
        base = f"{_base(hub_id)}/contents/{content_id}/revisions/{rev_id}/representations"
        reps = (await request("GET", base, auth="3lo", region=region)).get("results") or []
        if product_version:
            reps = [r for r in reps if str(r.get("productVersion")) == str(product_version)]
        if not reps:
            raise APSError(f"No representation{f' for version {product_version}' if product_version else ''}.")
        rep = max(reps, key=lambda r: str(r.get("productVersion") or ""))
        signed = await request("POST", f"{base}/{rep['id']}:get-download-url", params={"type": "CONTENT"},
                               auth="3lo", region=region)
        name = c.get("fileName") or f"{content_id}"
        dest = Path(dest_path).expanduser() if dest_path else config.DOWNLOAD_DIR
        if dest.is_dir() or not dest.suffix:
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / name
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", signed["url"]) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        fh.write(chunk)
        return ok({"name": name, "productVersion": rep.get("productVersion"), "savedTo": str(dest),
                   "size": dest.stat().st_size, "expectedSize": rep.get("fileSize")})
    except Exception as exc:
        return tool_error(exc)
