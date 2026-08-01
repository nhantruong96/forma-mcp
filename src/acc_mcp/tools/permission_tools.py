"""Folder permission tools (BIM 360 Docs permissions API — works for ACC)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field

from ..app import mcp
from ..http import ok, request, tool_error
from ..ids import enc, without_b
from .. import config
from .common import find_project_user

# ACC (unified) permission levels -> action sets
LEVELS: dict[str, list[str]] = {
    "view": ["VIEW", "COLLABORATE"],
    "view_download": ["VIEW", "DOWNLOAD", "COLLABORATE"],
    "create": ["VIEW", "DOWNLOAD", "COLLABORATE", "PUBLISH_MARKUP"],
    "upload": ["PUBLISH", "VIEW", "DOWNLOAD", "COLLABORATE", "PUBLISH_MARKUP"],
    "edit": ["PUBLISH", "VIEW", "DOWNLOAD", "COLLABORATE", "PUBLISH_MARKUP", "EDIT"],
    "manage": ["PUBLISH", "VIEW", "DOWNLOAD", "COLLABORATE", "PUBLISH_MARKUP", "EDIT", "CONTROL"],
}

_LEVEL_HELP = (
    "view (xem) | view_download (xem+tải) | create (xem+tải+markup) | "
    "upload (create+tải lên) | edit (sửa) | manage (toàn quyền)"
)


def _level_name(actions: list[str]) -> Optional[str]:
    aset = set(actions or [])
    for name, acts in LEVELS.items():
        if aset == set(acts):
            return name
    return None


def _perm_url(project_id: str, folder_id: str, suffix: str = "") -> str:
    return (
        f"{config.DOCS_V1}/projects/{without_b(project_id)}/folders/{enc(folder_id)}"
        f"/permissions{suffix}"
    )


async def _get_permissions(project_id: str, folder_id: str) -> list[dict[str, Any]]:
    payload = await request("GET", _perm_url(project_id, folder_id), impersonate_header="x-user-id")
    return payload if isinstance(payload, list) else payload.get("results", [])


async def _resolve_subject(project_id: str, subject: dict[str, Any]) -> dict[str, Any]:
    """Resolve {type, id?|email?|name?} to {subjectId, subjectType, autodeskId?}."""
    stype = (subject.get("type") or "USER").upper()
    if stype not in ("USER", "ROLE", "COMPANY"):
        raise ValueError(f"subject type must be USER, ROLE or COMPANY (got '{stype}')")
    if subject.get("id"):
        out = {"subjectId": str(subject["id"]), "subjectType": stype}
        if subject.get("autodeskId"):
            out["autodeskId"] = subject["autodeskId"]
        return out
    if stype == "USER" and subject.get("email"):
        user = await find_project_user(without_b(project_id), subject["email"])
        if not user:
            raise ValueError(
                f"No project member with email '{subject['email']}'. "
                "Add them first with acc_add_project_user."
            )
        return {"subjectId": user["id"], "subjectType": "USER", "autodeskId": user.get("autodeskId")}
    if stype in ("ROLE", "COMPANY") and subject.get("name"):
        raise ValueError(
            f"Provide the {stype.lower()} id for '{subject['name']}': role ids appear in "
            "acc_list_project_users (roles[].id); company ids in acc_list_companies."
        )
    raise ValueError(f"Cannot resolve subject {subject} — provide id, or email for USER.")


@mcp.tool(
    name="acc_get_folder_permissions",
    annotations={"title": "ACC: Get folder permissions", "readOnlyHint": True},
)
async def acc_get_folder_permissions(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
) -> str:
    """List who can access a folder (users / roles / companies), their
    permission level (named when it matches a standard ACC level) and whether
    it is direct or inherited from parent folders."""
    try:
        perms = await _get_permissions(project_id, folder_id)
        out = []
        for p in perms:
            direct = p.get("actions") or []
            inherited = p.get("inheritActions") or []
            effective = sorted(set(direct) | set(inherited))
            out.append(
                {
                    "subjectType": p.get("subjectType"),
                    "subjectId": p.get("subjectId"),
                    "autodeskId": p.get("autodeskId"),
                    "name": p.get("name"),
                    "email": p.get("email"),
                    "userType": p.get("userType"),
                    "level": _level_name(effective) or "custom",
                    "directActions": direct,
                    "inheritedActions": inherited,
                }
            )
        return ok(out, count=len(out), levels=_LEVEL_HELP)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_set_folder_permissions",
    annotations={"title": "ACC: Set folder permissions", "readOnlyHint": False},
)
async def acc_set_folder_permissions(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
    subjects_json: Annotated[str, Field(description=(
        'JSON array of subjects: [{"type": "USER", "email": "a@b.com"}, '
        '{"type": "USER", "id": "<guid>"}, {"type": "ROLE", "id": "<roleId>"}, '
        '{"type": "COMPANY", "id": "<companyId>"}]'
    ))],
    level: Annotated[str, Field(description=f"Permission level: {_LEVEL_HELP} — or 'custom'")] = "view_download",
    custom_actions: Annotated[Optional[str], Field(description="When level='custom': comma-separated actions from VIEW,DOWNLOAD,COLLABORATE,PUBLISH,PUBLISH_MARKUP,EDIT,CONTROL")] = None,
) -> str:
    """Grant or change folder permissions for users, roles or companies —
    the ISO 19650 access-control step of CDE setup (e.g. give the design team
    'upload' on WIP, the client 'view_download' on Published). Automatically
    uses batch-create for new subjects and batch-update for subjects that
    already hold direct permissions on the folder. Requires CONTROL ('manage')
    permission on the folder."""
    try:
        subjects = json.loads(subjects_json)
        if not isinstance(subjects, list) or not subjects:
            return "Error: subjects_json must be a non-empty JSON array."
        if level == "custom":
            if not custom_actions:
                return "Error: provide custom_actions when level='custom'."
            actions = [a.strip().upper() for a in custom_actions.split(",") if a.strip()]
        else:
            if level not in LEVELS:
                return f"Error: unknown level '{level}'. Use one of: {', '.join(LEVELS)} or 'custom'."
            actions = LEVELS[level]

        resolved = [await _resolve_subject(project_id, s) for s in subjects]
        existing = await _get_permissions(project_id, folder_id)
        existing_direct = {
            (p.get("subjectType"), p.get("subjectId"))
            for p in existing
            if p.get("actions")  # direct actions present
        }
        to_create, to_update = [], []
        for r in resolved:
            entry = {**r, "actions": actions}
            if (r["subjectType"], r["subjectId"]) in existing_direct:
                to_update.append(entry)
            else:
                to_create.append(entry)

        results: dict[str, Any] = {"granted": [], "updated": []}
        if to_create:
            payload = await request(
                "POST",
                _perm_url(project_id, folder_id, ":batch-create"),
                json_body=to_create,
                impersonate_header="x-user-id",
            )
            results["granted"] = payload.get("results", to_create)
        if to_update:
            payload = await request(
                "POST",
                _perm_url(project_id, folder_id, ":batch-update"),
                json_body=to_update,
                impersonate_header="x-user-id",
            )
            results["updated"] = payload.get("results", to_update)
        return ok(results, level=level, actions=actions,
                  note="Subfolders inherit these permissions unless overridden.")
    except json.JSONDecodeError as exc:
        return f"Error: subjects_json is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_remove_folder_permissions",
    annotations={"title": "ACC: Remove folder permissions", "readOnlyHint": False, "destructiveHint": True},
)
async def acc_remove_folder_permissions(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN")],
    subjects_json: Annotated[str, Field(description=(
        'JSON array of subjects to strip of DIRECT permissions on this folder, e.g. '
        '[{"type": "USER", "email": "a@b.com"}, {"type": "COMPANY", "id": "..."}]'
    ))],
) -> str:
    """Remove all DIRECT permissions of the given subjects on a folder.
    Inherited permissions from parent folders remain — remove them at the
    parent too if full revocation is needed. Project admins cannot be removed.
    Confirm with the user before calling."""
    try:
        subjects = json.loads(subjects_json)
        if not isinstance(subjects, list) or not subjects:
            return "Error: subjects_json must be a non-empty JSON array."
        resolved = [await _resolve_subject(project_id, s) for s in subjects]
        body = [{"subjectId": r["subjectId"], "subjectType": r["subjectType"]} for r in resolved]
        await request(
            "POST",
            _perm_url(project_id, folder_id, ":batch-delete"),
            json_body=body,
            impersonate_header="x-user-id",
        )
        return ok({"removedDirectPermissionsFor": body})
    except json.JSONDecodeError as exc:
        return f"Error: subjects_json is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)
