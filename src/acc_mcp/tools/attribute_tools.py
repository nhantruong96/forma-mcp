"""Document status / metadata tools: custom attributes, batch metadata, naming standard."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field

from ..app import mcp
from ..http import ok, request, tool_error
from ..ids import enc, with_b, without_b
from .. import config


async def _folder_attr_defs(project_id: str, folder_id: str) -> list[dict[str, Any]]:
    payload = await request(
        "GET",
        f"{config.DOCS_V1}/projects/{without_b(project_id)}/folders/{enc(folder_id)}/custom-attribute-definitions",
        params={"limit": 200},
        impersonate_header="x-user-id",
    )
    return payload.get("results", [])


@mcp.tool(
    name="acc_list_custom_attributes",
    annotations={"title": "ACC: List custom attribute definitions", "readOnlyHint": True},
)
async def acc_list_custom_attributes(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[Optional[str], Field(description="Folder URN — definitions applying to documents in this folder")] = None,
) -> str:
    """List custom attribute definitions (id, name, type, allowed values).
    With folder_id: the folder-level view (BIM 360 + ACC). Without: the ACC
    project-level registry, which also shows which definitions are bound to
    the file naming standard (namingStandardIds) — i.e. the ISO 19650
    Status/Revision/Classification metadata fields."""
    try:
        if folder_id:
            defs = await _folder_attr_defs(project_id, folder_id)
            return ok(defs, count=len(defs))
        payload = await request(
            "GET",
            f"{config.FILES_V1}/projects/{without_b(project_id)}/custom-attribute-definitions",
            params={"limit": 100},
            impersonate_header="x-user-id",
        )
        return ok(payload.get("results", payload), pagination=payload.get("pagination"))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_create_custom_attribute",
    annotations={"title": "ACC: Create custom attribute definition", "readOnlyHint": False},
)
async def acc_create_custom_attribute(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN the definition is created on (applies to documents in the folder tree)")],
    attr_name: Annotated[str, Field(description="Attribute name, unique within the folder, e.g. 'Trang thai (Status)'")],
    attr_type: Annotated[str, Field(description="string | date | array (array = drop-down list)")] = "string",
    array_values: Annotated[Optional[str], Field(description="For type 'array': comma-separated allowed values, e.g. 'S0,S1,S2,S3,S4,A1,B1,CR'")] = None,
) -> str:
    """Create a custom attribute definition on a folder — the way to add ISO
    19650 metadata fields (Status/Suitability, Revision, Uniclass code...) when
    the ACC file-naming standard is not used or extra fields are needed."""
    try:
        body: dict[str, Any] = {"name": attr_name, "type": attr_type}
        if attr_type == "array":
            if not array_values:
                return "Error: array type needs array_values."
            body["arrayValues"] = [v.strip() for v in array_values.split(",") if v.strip()]
        payload = await request(
            "POST",
            f"{config.DOCS_V1}/projects/{without_b(project_id)}/folders/{enc(folder_id)}/custom-attribute-definitions",
            json_body=body,
            impersonate_header="x-user-id",
        )
        return ok(payload, note="Definitions cannot be edited/deleted via API — only in the ACC UI.")
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_get_docs_metadata",
    annotations={"title": "ACC: Get document metadata (batch)", "readOnlyHint": True},
)
async def acc_get_docs_metadata(
    project_id: Annotated[str, Field(description="Project id")],
    urns: Annotated[str, Field(description="Comma- or newline-separated item (lineage) or version URNs, max 50. Item URNs resolve to their tip version")],
) -> str:
    """Batch-read document metadata: title, revision number, process state,
    review/approval status (from ACC review workflows) and all custom
    attribute VALUES (e.g. Status S3, Revision P01). The daily
    status-checking tool for a CDE manager."""
    try:
        urn_list = [u.strip() for u in urns.replace("\n", ",").split(",") if u.strip()]
        if not urn_list:
            return "Error: provide at least one URN."
        if len(urn_list) > 50:
            return "Error: max 50 URNs per call."
        payload = await request(
            "POST",
            f"{config.DOCS_V1}/projects/{without_b(project_id)}/versions:batch-get",
            json_body={"urns": urn_list},
            impersonate_header="x-user-id",
        )
        results = []
        for r in payload.get("results", []):
            results.append(
                {
                    "urn": r.get("urn"),
                    "itemUrn": r.get("itemUrn"),
                    "name": r.get("name"),
                    "title": r.get("title"),
                    "revisionNumber": r.get("revisionNumber"),
                    "processState": r.get("processState"),
                    "approvalStatus": r.get("approvalStatus"),
                    "customAttributes": [
                        {"id": a.get("id"), "name": a.get("name"), "value": a.get("value")}
                        for a in (r.get("customAttributes") or [])
                    ],
                    "createUserName": r.get("createUserName"),
                    "createTime": r.get("createTime"),
                }
            )
        return ok(results, errors=payload.get("errors") or [], count=len(results))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_set_custom_attributes",
    annotations={"title": "ACC: Set document attributes", "readOnlyHint": False},
)
async def acc_set_custom_attributes(
    project_id: Annotated[str, Field(description="Project id")],
    version_id: Annotated[str, Field(description="Version URN of the document (tip version for 'current')")],
    folder_id: Annotated[str, Field(description="Folder URN containing the document (used to resolve attribute names to ids)")],
    values_json: Annotated[str, Field(description=(
        'JSON object mapping attribute NAME or numeric id to value, e.g. '
        '{"Trang thai (Status)": "S3", "Revision": "P02", "1002": "2026-08-01"}. '
        "null clears a value. Drop-down values must match the defined list"
    ))],
) -> str:
    """Set custom attribute values on a document version — e.g. move a
    container from S2 to S3, stamp the Revision, or fill the Uniclass
    classification. Attribute names are resolved against the folder's
    definitions; unknown names are reported with the available ones."""
    try:
        values = json.loads(values_json)
        if not isinstance(values, dict) or not values:
            return "Error: values_json must be a non-empty JSON object."
        defs = await _folder_attr_defs(project_id, folder_id)
        by_name = {d["name"].strip().lower(): d for d in defs}
        by_id = {str(d["id"]): d for d in defs}
        batch = []
        problems = []
        for key, val in values.items():
            d = by_id.get(str(key)) or by_name.get(str(key).strip().lower())
            if not d:
                problems.append(key)
                continue
            if d.get("type") == "array" and val is not None and val not in (d.get("arrayValues") or []):
                return (
                    f"Error: '{val}' is not an allowed value for '{d['name']}'. "
                    f"Allowed: {', '.join(d.get('arrayValues') or [])}"
                )
            batch.append({"id": d["id"], "value": val})
        if problems:
            names = ", ".join(f"'{d['name']}' (id {d['id']})" for d in defs) or "(none defined on this folder)"
            return f"Error: unknown attribute(s): {', '.join(map(str, problems))}. Available: {names}"
        payload = await request(
            "POST",
            f"{config.DOCS_V1}/projects/{without_b(project_id)}/versions/{enc(version_id)}/custom-attributes:batch-update",
            json_body=batch,
            impersonate_header="x-user-id",
        )
        return ok(payload.get("results", payload))
    except json.JSONDecodeError as exc:
        return f"Error: values_json is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_get_naming_standard",
    annotations={"title": "ACC: Get project naming standard", "readOnlyHint": True},
)
async def acc_get_naming_standard(
    hub_id: Annotated[str, Field(description="Hub id")],
    project_id: Annotated[str, Field(description="Project id")],
) -> str:
    """Read the ACC file-naming standard configured on the project (the ISO
    19650 naming enforcement in ACC Docs): delimiter, fields with allowed
    codes, and metadata fields (Status/Revision/Classification). Read-only —
    the standard itself is configured in the ACC Docs UI (Settings > File
    naming standard). Useful for cross-checking against the project BEP
    convention."""
    try:
        tops = await request(
            "GET",
            f"{config.PROJECT_V1}/hubs/{with_b(hub_id)}/projects/{with_b(project_id)}/topFolders",
            impersonate_header="x-user-id",
        )
        ns_ids: list[str] = []
        for d in tops.get("data", []):
            ext = ((d.get("attributes") or {}).get("extension") or {}).get("data") or {}
            for nid in ext.get("namingStandardIds") or []:
                if nid not in ns_ids:
                    ns_ids.append(str(nid))
        if not ns_ids:
            return ok(
                {"namingStandard": None},
                note=(
                    "No file-naming standard is active on this project. Enable it in ACC Docs "
                    "UI (Files > Settings > Naming standard), or rely on acc_validate_names "
                    "with the project naming config for checking."
                ),
            )
        standards = []
        for nid in ns_ids:
            payload = await request(
                "GET",
                f"{config.DOCS_V1}/projects/{without_b(project_id)}/naming-standards/{nid}",
                impersonate_header="x-user-id",
            )
            standards.append(payload)
        return ok(standards, count=len(standards))
    except Exception as exc:
        return tool_error(exc)
