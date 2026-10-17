"""File tools: inspect, upload (new item or new version), download, copy."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
from pydantic import Field

from ..app import mcp
from ..http import APSError, ok, request, tool_error
from ..ids import enc, storage_parts, with_b
from .. import config
from .common import find_item_in_folder, fmt_item, fmt_version, included_versions_map

_CHUNK = 5 * 1024 * 1024  # 5 MB multipart chunks


@mcp.tool(
    name="acc_get_item",
    annotations={"title": "ACC: Get file info", "readOnlyHint": True},
)
async def acc_get_item(
    project_id: Annotated[str, Field(description="Project id")],
    item_id: Annotated[str, Field(description="Item (lineage) URN: urn:adsk.wipprod:dm.lineage:...")],
) -> str:
    """Get a file's info and its current (tip) version: version number, size,
    file type, last modified, process/review state and storage URN."""
    try:
        payload = await request(
            "GET",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/items/{enc(item_id)}",
            params={"includePathInProject": "true"},
            impersonate_header="x-user-id",
        )
        data = payload.get("data", {})
        vmap = included_versions_map(payload)
        out = fmt_item(data, vmap)
        tip_id = out.get("tipVersionId")
        if tip_id and tip_id in vmap:
            out["tipVersion"] = fmt_version(vmap[tip_id])
        path = ((data.get("meta") or {}).get("attributes") or {}).get("pathInProject")
        if path:
            out["pathInProject"] = path
        return ok(out)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_item_versions",
    annotations={"title": "ACC: List file versions", "readOnlyHint": True},
)
async def acc_list_item_versions(
    project_id: Annotated[str, Field(description="Project id")],
    item_id: Annotated[str, Field(description="Item (lineage) URN")],
    page_number: Annotated[int, Field(ge=0)] = 0,
    page_limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> str:
    """List the version history of a file (audit trail: who uploaded which
    version when) — supports the ISO 19650 traceability requirement."""
    try:
        payload = await request(
            "GET",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/items/{enc(item_id)}/versions",
            params={"page[number]": page_number, "page[limit]": page_limit},
            impersonate_header="x-user-id",
        )
        versions = [fmt_version(v) for v in payload.get("data", [])]
        has_more = bool(((payload.get("links") or {}).get("next") or {}).get("href"))
        return ok(versions, count=len(versions), hasMore=has_more)
    except Exception as exc:
        return tool_error(exc)


async def _upload_to_storage(project_id: str, folder_id: str, filename: str, path: Path) -> str:
    """POST storage, PUT signed S3 parts, complete. Returns the storage URN."""
    storage = await request(
        "POST",
        f"{config.DATA_V1}/projects/{with_b(project_id)}/storage",
        json_body={
            "jsonapi": {"version": "1.0"},
            "data": {
                "type": "objects",
                "attributes": {"name": filename},
                "relationships": {
                    "target": {"data": {"type": "folders", "id": folder_id}}
                },
            },
        },
        content_type="application/vnd.api+json",
        impersonate_header="x-user-id",
    )
    storage_urn = (storage.get("data") or {}).get("id")
    if not storage_urn:
        raise APSError("Storage creation returned no object id.")
    bucket, key = storage_parts(storage_urn)

    size = path.stat().st_size
    parts = max(1, math.ceil(size / _CHUNK))
    signed = await request(
        "GET",
        f"{config.OSS_V2}/buckets/{bucket}/objects/{enc(key)}/signeds3upload",
        params={"parts": parts, "minutesExpiration": 60},
        impersonate_header="x-user-id",
    )
    urls = signed.get("urls") or ([signed["url"]] if signed.get("url") else [])
    upload_key = signed.get("uploadKey")
    if not urls or not upload_key:
        raise APSError("signeds3upload returned no URLs/uploadKey.")

    async with httpx.AsyncClient(timeout=None) as client:
        with path.open("rb") as fh:
            for url in urls:
                chunk = fh.read(_CHUNK)
                resp = await client.put(url, content=chunk)
                if resp.status_code not in (200, 201):
                    raise APSError(f"S3 part upload failed ({resp.status_code}).")

    await request(
        "POST",
        f"{config.OSS_V2}/buckets/{bucket}/objects/{enc(key)}/signeds3upload",
        json_body={"uploadKey": upload_key, "size": size},
        impersonate_header="x-user-id",
    )
    return storage_urn


@mcp.tool(
    name="acc_upload_file",
    annotations={"title": "ACC: Upload file", "readOnlyHint": False},
)
async def acc_upload_file(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Destination folder URN")],
    local_path: Annotated[str, Field(description="Path of the file on this computer, e.g. 'C:/Projects/ABC/model.rvt'")],
    name: Annotated[Optional[str], Field(description=(
        "Name in ACC (defaults to the local file name). Use this ONLY to keep the CDE "
        "name unchanged when the local file is named differently — never to give an "
        "existing document a new name (that creates a separate item and loses its "
        "version history; use acc_rename_file instead)."
    ))] = None,
) -> str:
    """Upload a local file to an ACC Docs folder. If a file with the same name
    already exists there, a NEW VERSION is added — this is the ONLY correct way
    to update a document on a CDE (lineage and audit trail are preserved).
    Uploading under a different name creates a SEPARATE document and orphans the
    version history: never do that to 'replace' or 'rename' something.
    Tip: validate the name against the project naming convention first
    (acc_validate_names). Uses chunked S3 upload, so large models are fine.

    Reminder: only issued/shared containers belong on the CDE; keep work in
    progress outside it. After uploading a new version, stamp the metadata with
    acc_set_custom_attributes (Status / Revision / Classification / Description)
    — the file name itself stays the same."""
    try:
        path = Path(local_path).expanduser()
        if not path.is_file():
            return f"Error: local file not found: {path}"
        filename = (name or path.name).strip()

        storage_urn = await _upload_to_storage(project_id, folder_id, filename, path)
        existing_item = await find_item_in_folder(project_id, folder_id, filename)

        if existing_item:
            payload = await request(
                "POST",
                f"{config.DATA_V1}/projects/{with_b(project_id)}/versions",
                json_body={
                    "jsonapi": {"version": "1.0"},
                    "data": {
                        "type": "versions",
                        "attributes": {
                            "name": filename,
                            "extension": {"type": "versions:autodesk.bim360:File", "version": "1.0"},
                        },
                        "relationships": {
                            "item": {"data": {"type": "items", "id": existing_item}},
                            "storage": {"data": {"type": "objects", "id": storage_urn}},
                        },
                    },
                },
                content_type="application/vnd.api+json",
                impersonate_header="x-user-id",
            )
            v = payload.get("data", {})
            return ok(
                {
                    "action": "new_version",
                    "itemId": existing_item,
                    "versionId": v.get("id"),
                    "versionNumber": (v.get("attributes") or {}).get("versionNumber"),
                    "name": filename,
                }
            )

        payload = await request(
            "POST",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/items",
            json_body={
                "jsonapi": {"version": "1.0"},
                "data": {
                    "type": "items",
                    "attributes": {
                        "displayName": filename,
                        "extension": {"type": "items:autodesk.bim360:File", "version": "1.0"},
                    },
                    "relationships": {
                        "tip": {"data": {"type": "versions", "id": "1"}},
                        "parent": {"data": {"type": "folders", "id": folder_id}},
                    },
                },
                "included": [
                    {
                        "type": "versions",
                        "id": "1",
                        "attributes": {
                            "name": filename,
                            "extension": {"type": "versions:autodesk.bim360:File", "version": "1.0"},
                        },
                        "relationships": {
                            "storage": {"data": {"type": "objects", "id": storage_urn}}
                        },
                    }
                ],
            },
            content_type="application/vnd.api+json",
            impersonate_header="x-user-id",
        )
        item = payload.get("data", {})
        included = payload.get("included", [])
        version_id = included[0].get("id") if included else None
        return ok(
            {"action": "new_item", "itemId": item.get("id"), "versionId": version_id, "name": filename}
        )
    except Exception as exc:
        return tool_error(exc)


async def _resolve_storage(project_id: str, item_id: Optional[str], version_id: Optional[str]) -> tuple[str, str]:
    """Return (storage_urn, filename) for a version (or an item's tip)."""
    if version_id:
        payload = await request(
            "GET",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/versions/{enc(version_id)}",
            impersonate_header="x-user-id",
        )
        v = payload.get("data", {})
    elif item_id:
        payload = await request(
            "GET",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/items/{enc(item_id)}/tip",
            impersonate_header="x-user-id",
        )
        v = payload.get("data", {})
    else:
        raise ValueError("Provide item_id (downloads tip version) or version_id.")
    storage = (((v.get("relationships") or {}).get("storage") or {}).get("data") or {}).get("id")
    name = (v.get("attributes") or {}).get("displayName") or (v.get("attributes") or {}).get("name") or "download.bin"
    if not storage:
        raise APSError(
            "This version has no downloadable storage (it may be a cloud-native or "
            "extracted document)."
        )
    return storage, name


@mcp.tool(
    name="acc_rename_file",
    annotations={"title": "ACC: Rename file", "readOnlyHint": False},
)
async def acc_rename_file(
    project_id: Annotated[str, Field(description="Project id")],
    version_id: Annotated[str, Field(description="Version URN of the document (usually the tip version)")],
    new_name: Annotated[str, Field(min_length=1, max_length=255, description="New file name, including the extension")],
    confirmed_by_user: Annotated[bool, Field(description=(
        "Must be true. Rename only when the user has specifically asked for this file to "
        "be renamed — on a CDE a file name is the information-container ID and normally "
        "never changes."
    ))] = False,
) -> str:
    """Rename a document IN PLACE, keeping its item, version number and full
    history (PATCH on the version's name — the only method that works on ACC;
    PATCH on the item's displayName is rejected with BAD_INPUT).

    Use this instead of re-uploading under a different name: a new upload would
    create a separate document and orphan the version history. On a CDE the
    name carries the container ID only — status, revision, classification and
    description live in the metadata (acc_set_custom_attributes)."""
    if not confirmed_by_user:
        return (
            "Refused by the default CDE working rules: file names are information-container "
            "IDs and are not changed on our own initiative. Call again with "
            "confirmed_by_user=true only if the user specifically asked for this rename. "
            "To change status/revision/classification/description use "
            "acc_set_custom_attributes instead."
        )
    try:
        payload = await request(
            "PATCH",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/versions/{enc(version_id)}",
            json_body={
                "jsonapi": {"version": "1.0"},
                "data": {"type": "versions", "id": version_id, "attributes": {"name": new_name}},
            },
            content_type="application/vnd.api+json",
            impersonate_header="x-user-id",
        )
        return ok(fmt_version(payload.get("data", {})))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_download_file",
    annotations={"title": "ACC: Download file", "readOnlyHint": True},
)
async def acc_download_file(
    project_id: Annotated[str, Field(description="Project id")],
    item_id: Annotated[Optional[str], Field(description="Item URN — downloads its current (tip) version")] = None,
    version_id: Annotated[Optional[str], Field(description="Specific version URN (use instead of item_id for older versions)")] = None,
    dest_path: Annotated[Optional[str], Field(description="Destination file or folder on this computer (default: ~/.forma-mcp/downloads)")] = None,
    link_only: Annotated[bool, Field(description="True = return a temporary signed download URL instead of saving the file")] = False,
) -> str:
    """Download a document from ACC Docs to the local disk (or get a temporary
    signed URL with link_only=true, valid for a few minutes)."""
    try:
        storage, name = await _resolve_storage(project_id, item_id, version_id)
        bucket, key = storage_parts(storage)
        signed = await request(
            "GET",
            f"{config.OSS_V2}/buckets/{bucket}/objects/{enc(key)}/signeds3download",
            params={"minutesExpiration": 15},
            impersonate_header="x-user-id",
        )
        url = signed.get("url") or (signed.get("urls") or [None])[0]
        if not url:
            raise APSError(f"No signed URL returned (status: {signed.get('status')}).")
        if link_only:
            return ok({"name": name, "url": url, "expiresInMinutes": 15, "size": signed.get("size")})

        dest = Path(dest_path).expanduser() if dest_path else config.DOWNLOAD_DIR
        if dest.is_dir() or not dest.suffix:
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / name
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(1024 * 1024):
                        fh.write(chunk)
        return ok({"name": name, "savedTo": str(dest), "size": dest.stat().st_size})
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_copy_file",
    annotations={"title": "ACC: Copy file to folder", "readOnlyHint": False},
)
async def acc_copy_file(
    project_id: Annotated[str, Field(description="Project id")],
    version_id: Annotated[str, Field(description="Source version URN to copy (usually the tip version)")],
    target_folder_id: Annotated[str, Field(description="Destination folder URN (same project)")],
    new_name: Annotated[Optional[str], Field(description="Display name for the copy (default: same as source)")] = None,
) -> str:
    """Copy a file version into another folder of the same project as a NEW
    document — the typical WIP → Shared / Published hand-off when not using an
    ACC review workflow. (ACC cannot move items via API; copy then delete the
    original in the UI if a move is required.)"""
    try:
        if not new_name:
            payload = await request(
                "GET",
                f"{config.DATA_V1}/projects/{with_b(project_id)}/versions/{enc(version_id)}",
                impersonate_header="x-user-id",
            )
            new_name = ((payload.get("data") or {}).get("attributes") or {}).get("displayName") or "copy"
        payload = await request(
            "POST",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/items",
            params={"copyFrom": version_id},
            json_body={
                "jsonapi": {"version": "1.0"},
                "data": {
                    "type": "items",
                    "attributes": {
                        "displayName": new_name,
                        "extension": {"type": "items:autodesk.bim360:File", "version": "1.0"},
                    },
                    "relationships": {
                        "parent": {"data": {"type": "folders", "id": target_folder_id}}
                    },
                },
            },
            content_type="application/vnd.api+json",
            impersonate_header="x-user-id",
        )
        item = payload.get("data", {})
        return ok({"copiedItemId": item.get("id"), "name": new_name, "folderId": target_folder_id})
    except Exception as exc:
        return tool_error(exc)
