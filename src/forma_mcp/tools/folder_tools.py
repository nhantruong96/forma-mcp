"""Folder-structure tools (Data Management API, ACC Docs folders)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field

from ..app import mcp
from ..http import APSError, ok, request, tool_error
from ..ids import enc, with_b
from .. import config, naming
from .common import fmt_folder, fmt_item, included_versions_map, list_folder_contents

_FOLDER_EXT = {"type": "folders:autodesk.bim360:Folder", "version": "1.0"}


def _folder_body(name: str, parent_id: str) -> dict[str, Any]:
    return {
        "jsonapi": {"version": "1.0"},
        "data": {
            "type": "folders",
            "attributes": {"name": name, "extension": _FOLDER_EXT},
            "relationships": {"parent": {"data": {"type": "folders", "id": parent_id}}},
        },
    }


async def _create_folder(project_id: str, parent_id: str, name: str) -> dict[str, Any]:
    payload = await request(
        "POST",
        f"{config.DATA_V1}/projects/{with_b(project_id)}/folders",
        json_body=_folder_body(name, parent_id),
        content_type="application/vnd.api+json",
        impersonate_header="x-user-id",
    )
    return fmt_folder(payload.get("data", {}))


@mcp.tool(
    name="acc_get_top_folders",
    annotations={"title": "ACC: Top folders of a project", "readOnlyHint": True},
)
async def acc_get_top_folders(
    hub_id: Annotated[str, Field(description="Hub id (accountId, 'b.' optional)")],
    project_id: Annotated[str, Field(description="Project id ('b.' optional)")],
) -> str:
    """Get the root-level folders of a project (e.g. 'Project Files' / 'Plans'
    in ACC Docs). Start here to obtain folder URNs before listing or creating
    subfolders. Also reveals namingStandardIds when a file-naming standard is
    active."""
    try:
        payload = await request(
            "GET",
            f"{config.PROJECT_V1}/hubs/{with_b(hub_id)}/projects/{with_b(project_id)}/topFolders",
            impersonate_header="x-user-id",
        )
        folders = [fmt_folder(d) for d in payload.get("data", [])]
        return ok(folders, count=len(folders))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_folder",
    annotations={"title": "ACC: List folder contents", "readOnlyHint": True},
)
async def acc_list_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN (urn:adsk.wipprod:fs.folder:co...)")],
    filter_type: Annotated[Optional[str], Field(description="'folders' or 'items' to restrict")] = None,
    include_hidden: Annotated[bool, Field(description="Include deleted (hidden) entries")] = False,
    page_number: Annotated[int, Field(ge=0)] = 0,
    page_limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> str:
    """List the contents of a folder: subfolders and files with tip-version
    info (version number, size, last modified, process/review state, naming
    conformingStatus where available)."""
    try:
        result = await list_folder_contents(
            project_id,
            folder_id,
            page_number=page_number,
            page_limit=page_limit,
            filter_type=filter_type,
            include_hidden=include_hidden,
        )
        return ok(result["entries"], hasMore=result["hasMore"], pageNumber=page_number)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_create_folder",
    annotations={"title": "ACC: Create folder", "readOnlyHint": False},
)
async def acc_create_folder(
    project_id: Annotated[str, Field(description="Project id")],
    parent_folder_id: Annotated[str, Field(description="Parent folder URN")],
    name: Annotated[str, Field(min_length=1, max_length=255, description="New folder name (no <>:\"/\\|?* characters)")],
) -> str:
    """Create a single subfolder under a parent folder. For whole CDE trees use
    acc_create_folder_structure instead."""
    try:
        folder = await _create_folder(project_id, parent_folder_id, name)
        return ok(folder)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_create_folder_structure",
    annotations={"title": "ACC: Create folder structure (CDE)", "readOnlyHint": False},
)
async def acc_create_folder_structure(
    project_id: Annotated[str, Field(description="Project id")],
    parent_folder_id: Annotated[str, Field(description="Folder URN under which the structure is created (usually 'Project Files')")],
    structure: Annotated[str, Field(description=(
        "Either a naming-config reference like 'config:iso19650-uk' (uses its "
        "cdeStructure: WIP/Shared/Published/Archived + subfolders), or an inline "
        "JSON object of nested folders, e.g. "
        '{"01-WIP": {"01. Architecture": {}, "02. Structure": {}}, "02-SHARED": {}}. '
        "Build a custom tree from discipline/milestone lists with acc_cde_structure_template."
    ))],
    dry_run: Annotated[bool, Field(description="True = only show what would be created")] = False,
) -> str:
    """Create a whole (nested) folder tree in one call — the core of CDE setup
    per ISO 19650 (WIP / Shared / Published / Archived containers). Existing
    folders with the same name are reused, so the tool is safe to re-run
    (idempotent). Returns the created/existing folder URNs level by level."""
    try:
        if structure.strip().lower().startswith("config:"):
            cfg = naming.load_config(structure.split(":", 1)[1].strip())
            tree = cfg.get("cdeStructure")
            if not tree:
                return f"Error: naming config '{cfg.get('name')}' has no cdeStructure."
        else:
            tree = json.loads(structure)
        if not isinstance(tree, dict) or not tree:
            return "Error: structure must be a non-empty JSON object of nested folder names."

        created: list[dict[str, Any]] = []
        skipped: list[str] = []

        async def walk(parent_id: str, subtree: dict[str, Any], path: str) -> None:
            existing: dict[str, str] = {}
            listing = await list_folder_contents(project_id, parent_id, filter_type="folders", page_limit=200)
            for e in listing["entries"]:
                existing[(e.get("name") or "").strip().lower()] = e["id"]
            for name, children in subtree.items():
                full = f"{path}/{name}"
                fid = existing.get(name.strip().lower())
                if fid:
                    skipped.append(full)
                elif dry_run:
                    created.append({"path": full, "id": "(dry-run)"})
                    fid = None
                else:
                    folder = await _create_folder(project_id, parent_id, name)
                    fid = folder["id"]
                    created.append({"path": full, "id": fid})
                if isinstance(children, dict) and children:
                    if fid:
                        await walk(fid, children, full)
                    elif dry_run:
                        # can't descend into a not-yet-created folder; still preview
                        def preview(sub: dict[str, Any], p: str) -> None:
                            for n, ch in sub.items():
                                created.append({"path": f"{p}/{n}", "id": "(dry-run)"})
                                if isinstance(ch, dict) and ch:
                                    preview(ch, f"{p}/{n}")
                        preview(children, full)

        await walk(parent_folder_id, tree, "")
        return ok(
            {"created": created, "reusedExisting": skipped},
            dryRun=dry_run,
            note="Next steps: set folder permissions (acc_set_folder_permissions) and, if needed, custom attributes per folder.",
        )
    except json.JSONDecodeError as exc:
        return f"Error: structure is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_rename_folder",
    annotations={"title": "ACC: Rename folder", "readOnlyHint": False},
)
async def acc_rename_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
    new_name: Annotated[str, Field(min_length=1, max_length=255)],
    confirmed_by_user: Annotated[bool, Field(description=(
        "Must be true. Rename only when the user has specifically asked for this folder "
        "to be renamed."
    ))] = False,
) -> str:
    """Rename a folder (contents and permissions unaffected).

    KNOWN ACC LIMITATION — verified: this PATCH updates the folder's internal
    'name' but NOT the 'displayName' that ACC Docs (and this server's folder
    listings) show, so the new name will not appear in the UI. Renaming a folder
    reliably has to be done by the user in the ACC Docs UI. Never emulate a
    rename by deleting and re-creating the folder — deleting is not allowed on a
    CDE. Rename only on the user's specific request."""
    if not confirmed_by_user:
        return (
            "Refused by the default CDE working rules: rename only when the user has "
            "specifically asked for it. Also note that folder renaming does not work "
            "through the ACC API (displayName is unchanged) — ask the user to rename the "
            "folder in the ACC Docs UI."
        )
    try:
        payload = await request(
            "PATCH",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}",
            json_body={
                "jsonapi": {"version": "1.0"},
                "data": {"type": "folders", "id": folder_id, "attributes": {"name": new_name}},
            },
            content_type="application/vnd.api+json",
            impersonate_header="x-user-id",
        )
        return ok(
            fmt_folder(payload.get("data", {})),
            note=(
                "WARNING: ACC Docs keeps showing the previous folder name (displayName is "
                "not updated by the API). Ask the user to rename the folder in the ACC "
                "Docs UI if the new name has to be visible."
            ),
        )
    except Exception as exc:
        return tool_error(exc)


async def _set_hidden(project_id: str, folder_id: str, hidden: bool) -> dict[str, Any]:
    payload = await request(
        "PATCH",
        f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}",
        json_body={
            "jsonapi": {"version": "1.0"},
            "data": {"type": "folders", "id": folder_id, "attributes": {"hidden": hidden}},
        },
        content_type="application/vnd.api+json",
        impersonate_header="x-user-id",
    )
    return fmt_folder(payload.get("data", {}))


@mcp.tool(
    name="acc_delete_folder",
    annotations={"title": "ACC: Delete (hide) folder", "readOnlyHint": False, "destructiveHint": True},
)
async def acc_delete_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
    confirmed_by_user: Annotated[bool, Field(description=(
        "Must be true. Set it ONLY after the user has explicitly asked for this folder "
        "to be deleted — deleting is against the default CDE working rules."
    ))] = False,
) -> str:
    """Delete a folder in ACC Docs (soft delete — hidden, restorable with
    acc_restore_folder).

    AGAINST THE DEFAULT CDE RULES: nothing is deleted on a CDE. Do not use this
    to 'clean up' or to work around the fact that folders cannot be renamed
    through the API — ask the user to rename the folder in the ACC Docs UI
    instead. Requires confirmed_by_user=true."""
    if not confirmed_by_user:
        return (
            "Refused by the default CDE working rules: folders are never deleted on a "
            "CDE (superseded content is moved to the Archived area). If the user has "
            "explicitly asked for this deletion, call again with confirmed_by_user=true. "
            "To rename a folder, ask the user to do it in the ACC Docs UI — the API "
            "cannot change a folder's display name."
        )
    try:
        folder = await _set_hidden(project_id, folder_id, True)
        return ok(folder, note="Soft-deleted (hidden). Restore with acc_restore_folder.")
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_restore_folder",
    annotations={"title": "ACC: Restore folder", "readOnlyHint": False},
)
async def acc_restore_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
) -> str:
    """Restore a previously deleted (hidden) folder."""
    try:
        folder = await _set_hidden(project_id, folder_id, False)
        return ok(folder)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_move_folder",
    annotations={"title": "ACC: Move folder", "readOnlyHint": False},
)
async def acc_move_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN to move")],
    new_parent_folder_id: Annotated[str, Field(description="Destination parent folder URN")],
) -> str:
    """Move a folder (with its contents) under another parent folder. Folder
    permissions defined directly on the folder move with it; inherited ones
    re-derive from the new parent — re-check with acc_get_folder_permissions."""
    try:
        payload = await request(
            "PATCH",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}",
            json_body={
                "jsonapi": {"version": "1.0"},
                "data": {
                    "type": "folders",
                    "id": folder_id,
                    "relationships": {
                        "parent": {"data": {"type": "folders", "id": new_parent_folder_id}}
                    },
                },
            },
            content_type="application/vnd.api+json",
            impersonate_header="x-user-id",
        )
        return ok(fmt_folder(payload.get("data", {})))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_search_folder",
    annotations={"title": "ACC: Search in folder tree", "readOnlyHint": True},
)
async def acc_search_folder(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN to search under (recursive)")],
    name: Annotated[Optional[str], Field(description="Exact display name to find, e.g. 'ABC-XYZ-ZZ-XX-M3-S-0001.rvt'")] = None,
    file_type: Annotated[Optional[str], Field(description="File extension filter, e.g. 'rvt', 'ifc', 'pdf'")] = None,
    page_number: Annotated[int, Field(ge=0, le=49)] = 0,
) -> str:
    """Search recursively under a folder for files by exact name and/or type.
    Returns matching tip versions with their item (lineage) URNs."""
    try:
        params: dict[str, Any] = {"page[number]": page_number}
        if name:
            params["filter[attributes.displayName]"] = name
        if file_type:
            params["filter[fileType]"] = file_type
        if not name and not file_type:
            return "Error: provide at least one of name or file_type."
        payload = await request(
            "GET",
            f"{config.DATA_V1}/projects/{with_b(project_id)}/folders/{enc(folder_id)}/search",
            params=params,
            impersonate_header="x-user-id",
        )
        vmap = included_versions_map(payload)
        items = [fmt_item(i, vmap) for i in payload.get("included", []) if i.get("type") == "items"]
        if not items:
            # versions come back in data; associate to their item ids
            out = []
            for v in payload.get("data", []):
                attrs = v.get("attributes", {})
                item_id = (((v.get("relationships") or {}).get("item") or {}).get("data") or {}).get("id")
                out.append(
                    {
                        "name": attrs.get("displayName"),
                        "itemId": item_id,
                        "versionId": v.get("id"),
                        "versionNumber": attrs.get("versionNumber"),
                    }
                )
            items = out
        return ok(items, count=len(items))
    except Exception as exc:
        return tool_error(exc)
