"""ISO 19650 container-ID naming engine: load configs, validate, decode, compose.

A naming config is a JSON document (see naming_configs/) with:
  delimiter, fields[] (each with key/name and either a codes table and/or a
  regex pattern, plus allowUnlisted), optional statusSuffix, statusCodes,
  revisionCodes, classification, cdeStates, cdeStructure.
"""

from __future__ import annotations

import difflib
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Optional

from . import config

# File extensions stripped before validating a container ID
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def _bundled_dir() -> Any:
    return resources.files("forma_mcp") / "naming_configs"


def list_configs() -> list[dict[str, str]]:
    """List available naming configs (bundled + user dir)."""
    found: dict[str, dict[str, str]] = {}
    try:
        for item in _bundled_dir().iterdir():
            if item.name.endswith(".json"):
                found[item.name[:-5]] = {"id": item.name[:-5], "source": "bundled"}
    except (FileNotFoundError, OSError):
        pass
    if config.NAMING_DIR.is_dir():
        for p in sorted(config.NAMING_DIR.glob("*.json")):
            found[p.stem] = {"id": p.stem, "source": str(p)}
    out = []
    for cid, meta in sorted(found.items()):
        try:
            cfg = load_config(cid)
            meta["name"] = cfg.get("name", cid)
            meta["fields"] = " - ".join(f["key"] for f in cfg.get("fields", []))
        except Exception as exc:  # show broken configs rather than hiding them
            meta["name"] = f"(invalid: {exc})"
        out.append(meta)
    return out


def load_config(name_or_path: Optional[str] = None) -> dict[str, Any]:
    """Load a naming config by id, filename or path. Default: FORMA_MCP_NAMING_CONFIG."""
    ref = (name_or_path or config.DEFAULT_NAMING_CONFIG).strip()
    candidates: list[Path] = []
    if ref.endswith(".json"):
        candidates.append(Path(ref).expanduser())
        candidates.append(config.NAMING_DIR / ref)
    else:
        candidates.append(config.NAMING_DIR / f"{ref}.json")
    for cand in candidates:
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    # bundled fallback
    stem = ref[:-5] if ref.endswith(".json") else ref
    try:
        res = _bundled_dir() / f"{stem}.json"
        return json.loads(res.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        available = ", ".join(c["id"] for c in list_configs()) or "(none)"
        raise ValueError(
            f"Naming config '{ref}' not found. Available: {available}. "
            f"Add custom configs as JSON files in {config.NAMING_DIR}"
        ) from exc


def build_cde_structure(
    disciplines: Optional[list[str]] = None,
    milestones: Optional[list[str]] = None,
    shared_subfolders: Optional[list[str]] = None,
    wip_name: str = "01-WIP",
    shared_name: str = "02-SHARED",
    published_name: str = "03-PUBLISHED",
    archived_name: str = "04-ARCHIVED",
    numbered: bool = True,
) -> dict[str, Any]:
    """Build a generic ISO 19650 CDE folder tree (WIP/Shared/Published/Archived).

    disciplines become subfolders of WIP, milestones become subfolders of
    Published, shared_subfolders become subfolders of Shared. Any list left
    empty yields an empty container. numbered=True prefixes children with
    '01. ', '02. ', ... in order.
    """
    def _children(items: Optional[list[str]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for idx, raw in enumerate(items or [], start=1):
            name = str(raw).strip()
            if not name:
                continue
            if numbered:
                name = f"{idx:02d}. {name}"
            out[name] = {}
        return out

    return {
        wip_name: _children(disciplines),
        shared_name: _children(shared_subfolders),
        published_name: _children(milestones),
        archived_name: {},
    }


def strip_extension(filename: str) -> tuple[str, str]:
    """Return (base, extension) — extension may be ''. Only the last .xxx is stripped."""
    m = _EXT_RE.search(filename)
    if m:
        return filename[: m.start()], filename[m.start():]
    return filename, ""


def _check_field(field: dict[str, Any], value: str) -> dict[str, Any]:
    """Validate one field value; returns {ok, level, meaning?, message?, suggestions?}."""
    codes: dict[str, str] = field.get("codes") or {}
    pattern: Optional[str] = field.get("pattern")
    allow_unlisted: bool = bool(field.get("allowUnlisted", False))

    if value in codes:
        return {"ok": True, "level": "ok", "meaning": codes[value]}
    if pattern and re.fullmatch(pattern, value):
        if codes and not allow_unlisted:
            sugg = difflib.get_close_matches(value, codes.keys(), n=3, cutoff=0.4)
            return {
                "ok": False,
                "level": "error",
                "message": f"'{value}' matches the pattern but is not in the approved code table",
                "suggestions": sugg,
            }
        level = "warning" if codes else "ok"
        return {
            "ok": True,
            "level": level,
            "meaning": field.get("patternHint", "matches pattern"),
            "message": None if level == "ok" else f"'{value}' is not in the code table (pattern accepted)",
        }
    if codes:
        sugg = difflib.get_close_matches(value.upper(), [c.upper() for c in codes], n=3, cutoff=0.4)
        # map back to original case
        orig = [c for c in codes if c.upper() in sugg]
        return {
            "ok": False,
            "level": "error",
            "message": f"'{value}' is not a valid code",
            "suggestions": orig or list(codes.keys())[:5],
        }
    return {
        "ok": False,
        "level": "error",
        "message": f"'{value}' does not match pattern {pattern}" + (
            f" ({field['patternHint']})" if field.get("patternHint") else ""
        ),
    }


def validate_name(filename: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate a container ID / file name against a naming config."""
    base, ext = strip_extension(filename.strip())
    delim = cfg.get("delimiter", "-")
    fields: list[dict[str, Any]] = cfg.get("fields", [])
    parts = base.split(delim)

    n = len(fields)
    suffix_cfg = cfg.get("statusSuffix") or {}
    suffix_enabled = bool(suffix_cfg.get("enabled"))
    result: dict[str, Any] = {
        "input": filename,
        "base": base,
        "extension": ext or None,
        "config": cfg.get("name"),
        "valid": True,
        "errors": [],
        "warnings": [],
        "fields": [],
    }

    status = revision = None
    if len(parts) == n:
        pass
    elif suffix_enabled and len(parts) == n + 2:
        status, revision = parts[n], parts[n + 1]
        parts = parts[:n]
    else:
        expected = f"{n} fields" + (f" (or {n + 2} with -Status-Revision suffix)" if suffix_enabled else "")
        result["valid"] = False
        result["errors"].append(
            f"Found {len(parts)} parts separated by '{delim}', expected {expected}. "
            f"Structure: {delim.join('<' + f.get('name', f['key']) + '>' for f in fields)}"
        )
        return result

    for field, value in zip(fields, parts):
        check = _check_field(field, value)
        entry = {
            "field": field.get("name", field["key"]),
            "key": field["key"],
            "value": value,
            "meaning": check.get("meaning"),
        }
        if check["level"] == "error":
            result["valid"] = False
            msg = f"[{field['key']}] {check['message']}"
            if check.get("suggestions"):
                msg += f" — did you mean: {', '.join(check['suggestions'])}?"
            result["errors"].append(msg)
            entry["error"] = check["message"]
            entry["suggestions"] = check.get("suggestions")
        elif check["level"] == "warning":
            result["warnings"].append(f"[{field['key']}] {check['message']}")
        result["fields"].append(entry)

    if status is not None:
        sp = suffix_cfg.get("statusPattern")
        rp = suffix_cfg.get("revisionPattern")
        smeaning = (cfg.get("statusCodes") or {}).get(status)
        if not smeaning:
            # A2..An / B2..Bn share the A1/B1 description
            base_code = status[0] + "1" if status[:1] in ("A", "B") and status[1:].isdigit() else None
            smeaning = (cfg.get("statusCodes") or {}).get(base_code or "", None)
        if sp and not re.fullmatch(sp, status):
            result["valid"] = False
            result["errors"].append(f"[status] '{status}' does not match {sp}")
        result["fields"].append({"field": "Status (suitability)", "key": "status", "value": status, "meaning": smeaning})
        if rp and not re.fullmatch(rp, revision or ""):
            result["valid"] = False
            result["errors"].append(f"[revision] '{revision}' does not match {rp}")
        result["fields"].append({"field": "Revision", "key": "revision", "value": revision, "meaning": None})

    return result


def compose_name(values: dict[str, str], cfg: dict[str, Any],
                 status: Optional[str] = None, revision: Optional[str] = None) -> dict[str, Any]:
    """Build a container ID from per-field values; validates as it goes."""
    delim = cfg.get("delimiter", "-")
    parts: list[str] = []
    errors: list[str] = []
    for field in cfg.get("fields", []):
        key = field["key"]
        val = (values.get(key) or "").strip()
        if not val:
            errors.append(f"Missing value for field '{key}' ({field.get('name', key)})")
            continue
        check = _check_field(field, val)
        if check["level"] == "error":
            msg = f"[{key}] {check['message']}"
            if check.get("suggestions"):
                msg += f" — did you mean: {', '.join(check['suggestions'])}?"
            errors.append(msg)
        parts.append(val)
    name = delim.join(parts)
    if status or revision:
        if not (status and revision):
            errors.append("Provide BOTH status and revision for the suffix, or neither.")
        else:
            name = f"{name}{delim}{status}{delim}{revision}"
    out = {"name": name, "valid": not errors, "errors": errors}
    if not errors:
        out["decoded"] = validate_name(name, cfg)["fields"]
    return out
