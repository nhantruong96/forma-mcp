"""ISO 19650 / TCVN 14177 naming tools: validate, compose, audit, configs."""

from __future__ import annotations

import json
from typing import Annotated, Any, Optional

from pydantic import Field

from ..app import mcp
from ..http import ok, tool_error
from .. import naming
from .common import list_folder_contents


@mcp.tool(
    name="acc_naming_configs",
    annotations={"title": "Naming: List conventions", "readOnlyHint": True},
)
async def acc_naming_configs(
    config_id: Annotated[Optional[str], Field(description="Show full detail of one config (code tables, status codes, CDE structure)")] = None,
) -> str:
    """List the available container-ID naming conventions and the current
    default. Bundled presets: 'iso19650-uk' (generic UK BIM Framework, the
    default). Each project adds its own convention by dropping a JSON file
    named after the project (e.g. '<project-code>.json') in ~/.forma-mcp/naming,
    then selecting it via config_id or FORMA_MCP_NAMING_CONFIG. With config_id,
    returns the full convention: field code tables, status &
    revision codes, classification system and the default CDE folder
    structure."""
    try:
        if config_id:
            return ok(naming.load_config(config_id))
        return ok(naming.list_configs(), defaultConfig=naming.config.DEFAULT_NAMING_CONFIG)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_validate_names",
    annotations={"title": "Naming: Validate container IDs", "readOnlyHint": True},
)
async def acc_validate_names(
    names: Annotated[str, Field(description="File/container names to check, separated by newlines or commas, e.g. 'ABC-XYZ-ZZ-XX-DR-A-0001.pdf'")],
    config_id: Annotated[Optional[str], Field(description="Naming config to check against (default: the server default config)")] = None,
) -> str:
    """Validate file / information-container names against the project's ISO
    19650 naming convention: field-by-field decoding with meanings, precise
    errors and closest-code suggestions. Accepts the optional
    -Status-Revision suffix (e.g. ...-0001-S3-P01). Use before uploading or
    when checking a delivery list."""
    try:
        cfg = naming.load_config(config_id)
        name_list = [n.strip() for n in names.replace(",", "\n").splitlines() if n.strip()]
        if not name_list:
            return "Error: provide at least one name."
        results = [naming.validate_name(n, cfg) for n in name_list]
        n_ok = sum(1 for r in results if r["valid"])
        return ok(
            results,
            summary={"total": len(results), "valid": n_ok, "invalid": len(results) - n_ok},
            config=cfg.get("name"),
        )
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_compose_name",
    annotations={"title": "Naming: Compose container ID", "readOnlyHint": True},
)
async def acc_compose_name(
    values_json: Annotated[str, Field(description=(
        "JSON object of field values keyed by each config field's 'key' (run "
        "acc_naming_configs with a config_id to see the keys). Example for a "
        "7-field ISO 19650 config: {\"project\": \"ABC\", \"originator\": \"XYZ\", "
        "\"volume\": \"ZZ\", \"level\": \"XX\", \"type\": \"DR\", \"role\": \"A\", "
        "\"number\": \"0001\"}"
    ))],
    status: Annotated[Optional[str], Field(description="Optional suitability code for the suffix, e.g. 'S3'")] = None,
    revision: Annotated[Optional[str], Field(description="Optional revision for the suffix, e.g. 'P01'")] = None,
    config_id: Annotated[Optional[str], Field(description="Naming config (default: the server default config)")] = None,
) -> str:
    """Build a correct container ID from field values (validated against the
    code tables), optionally with the -Status-Revision suffix. Use when
    creating a new deliverable name — check the next free sequence number with
    acc_search_folder first."""
    try:
        cfg = naming.load_config(config_id)
        values = json.loads(values_json)
        result = naming.compose_name(values, cfg, status=status, revision=revision)
        return ok(result, config=cfg.get("name"))
    except json.JSONDecodeError as exc:
        return f"Error: values_json is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_audit_folder_naming",
    annotations={"title": "Naming: Audit folder compliance", "readOnlyHint": True},
)
async def acc_audit_folder_naming(
    project_id: Annotated[str, Field(description="Project id")],
    folder_id: Annotated[str, Field(description="Folder URN to audit")],
    recursive: Annotated[bool, Field(description="Also audit subfolders")] = True,
    max_folders: Annotated[int, Field(ge=1, le=100, description="Safety cap on folders visited")] = 30,
    config_id: Annotated[Optional[str], Field(description="Naming config (default: the server default config)")] = None,
) -> str:
    """Audit an ACC folder (tree) for naming compliance: every file name is
    validated against the ISO 19650 convention; ACC's own conformingStatus is
    reported when a naming standard is active. Returns compliance stats plus
    the list of non-conforming files with reasons — a standards/metadata QA
    check for routine (daily/weekly) CDE review."""
    try:
        cfg = naming.load_config(config_id)
        audited: list[dict[str, Any]] = []
        non_conforming: list[dict[str, Any]] = []
        folders_visited = 0
        queue: list[tuple[str, str]] = [(folder_id, "")]

        while queue and folders_visited < max_folders:
            fid, path = queue.pop(0)
            folders_visited += 1
            page = 0
            while True:
                listing = await list_folder_contents(project_id, fid, page_number=page, page_limit=200)
                for e in listing["entries"]:
                    if e["type"] == "folder":
                        if recursive:
                            queue.append((e["id"], f"{path}/{e['name']}"))
                        continue
                    name = e.get("name") or ""
                    check = naming.validate_name(name, cfg)
                    entry: dict[str, Any] = {
                        "name": name,
                        "path": path or "/",
                        "valid": check["valid"],
                        "itemId": e.get("id"),
                    }
                    if e.get("conformingStatus"):
                        entry["accConformingStatus"] = e["conformingStatus"]
                    if not check["valid"]:
                        entry["errors"] = check["errors"]
                        non_conforming.append(entry)
                    audited.append(entry)
                if not listing["hasMore"]:
                    break
                page += 1

        total = len(audited)
        valid = sum(1 for a in audited if a["valid"])
        return ok(
            {
                "summary": {
                    "filesAudited": total,
                    "conforming": valid,
                    "nonConforming": total - valid,
                    "complianceRate": round(100 * valid / total, 1) if total else None,
                    "foldersVisited": folders_visited,
                    "truncated": bool(queue),
                },
                "nonConformingFiles": non_conforming[:100],
            },
            config=cfg.get("name"),
        )
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_cde_structure_template",
    annotations={"title": "Naming: Build CDE folder template", "readOnlyHint": True},
)
async def acc_cde_structure_template(
    disciplines: Annotated[Optional[str], Field(description="Comma-separated WIP discipline subfolders, e.g. 'Architecture,Structure,MEP,Civil'")] = None,
    milestones: Annotated[Optional[str], Field(description="Comma-separated Published milestone subfolders, e.g. 'Stage 2,Stage 3,Stage 4'")] = None,
    shared_subfolders: Annotated[Optional[str], Field(description="Comma-separated Shared subfolders, e.g. 'Models,Drawings,Reports'")] = None,
    state_names: Annotated[Optional[str], Field(description="Override the 4 top-level state names (comma-separated, order: WIP,Shared,Published,Archived). Default: 01-WIP,02-SHARED,03-PUBLISHED,04-ARCHIVED")] = None,
    numbered: Annotated[bool, Field(description="Prefix subfolders with '01. ', '02. ' in order")] = True,
    from_config: Annotated[Optional[str], Field(description="Instead of the lists, return the cdeStructure defined in this naming config (e.g. 'iso19650-uk' or a project config id)")] = None,
) -> str:
    """Generate a generic ISO 19650 CDE folder structure (WIP / Shared /
    Published / Archived) from simple lists — project-agnostic, so any project
    can build its own tree. Returns a JSON structure ready to pass to
    acc_create_folder_structure (review it first, then create). Give the WIP
    disciplines, the Published milestones and the Shared subfolders for the
    project; or use from_config to pull a convention's predefined cdeStructure."""
    try:
        if from_config:
            cfg = naming.load_config(from_config)
            tree = cfg.get("cdeStructure")
            if not tree:
                return f"Error: config '{from_config}' has no cdeStructure."
            return ok({"structure": tree, "source": from_config},
                      structureJson=json.dumps(tree, ensure_ascii=False))

        def _split(s: Optional[str]) -> Optional[list[str]]:
            if not s:
                return None
            return [x.strip() for x in s.split(",") if x.strip()]

        kwargs: dict[str, Any] = {
            "disciplines": _split(disciplines),
            "milestones": _split(milestones),
            "shared_subfolders": _split(shared_subfolders),
            "numbered": numbered,
        }
        states = _split(state_names)
        if states:
            if len(states) != 4:
                return "Error: state_names must list exactly 4 names (WIP,Shared,Published,Archived)."
            kwargs.update(
                wip_name=states[0], shared_name=states[1],
                published_name=states[2], archived_name=states[3],
            )
        tree = naming.build_cde_structure(**kwargs)
        return ok(
            {"structure": tree},
            structureJson=json.dumps(tree, ensure_ascii=False),
            note="Pass structureJson as the 'structure' argument of acc_create_folder_structure to create these folders.",
        )
    except Exception as exc:
        return tool_error(exc)
