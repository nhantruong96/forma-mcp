#!/usr/bin/env python3
"""Generate an ACC "File naming standard" import .xlsx from an acc-mcp naming config.

ACC Docs lets an admin IMPORT a naming standard as an Excel workbook (the
"Default - ISO 19650" template). The APS API cannot create/edit a naming
standard, so this script produces that workbook for a project from the same
JSON naming config the MCP uses for validation — keeping the ACC standard and
the MCP's checks in sync. Deliver the output .xlsx to the user; they upload it
in ACC Docs > Settings > Naming standard > Import.

Usage:
    python gen_acc_naming_template.py <config_id_or_path> <output.xlsx> \
        [--name "Standard name"] [--status S0:desc,S1:desc,...]

The 7 naming fields of the config map, in order, onto the ACC tabs:
    Project, Originator, VolumeSystem, LevelLocation, Type, Role, Number
Status/Revision/Classification come from the config's status/revision/
classification sections (Classification tab is left as the ACC Uniclass
default unless the config overrides it).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

import openpyxl

_HERE = Path(__file__).resolve().parent
MASTER = _HERE.parent / "naming_templates" / "Default_ISO_19650_master.xlsx"
_CONFIG_DIRS = [
    _HERE.parent / "src" / "acc_mcp" / "naming_configs",  # bundled presets
    Path.home() / ".acc-mcp" / "naming",                   # user project configs
]

DELIM_WORD = {"-": "Hyphen", "_": "Underscore", ".": "Period"}

# ACC naming-convention tabs, in file-name order → config.fields index
NAMING_TABS = ["Project", "Originator", "VolumeSystem", "LevelLocation", "Type", "Role", "Number"]

# rows to clear when replacing a drop-down list's values
_CLEAR_TO = 500


def load_config(ref: str) -> dict[str, Any]:
    p = Path(ref)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    stem = ref[:-5] if ref.endswith(".json") else ref
    for d in _CONFIG_DIRS:
        cand = d / f"{stem}.json"
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    raise SystemExit(f"Config '{ref}' not found (looked in {_CONFIG_DIRS}).")


def parse_char(field: dict[str, Any]) -> tuple[str, str]:
    """Return (char_type, req_char) for a text-field tab from a config field."""
    pat = field.get("pattern") or ""
    m = re.fullmatch(r"\^?\[0-9\]\{(\d+)(?:,(\d+))?\}\$?", pat)
    if m:
        lo, hi = m.group(1), m.group(2)
        return "Numeric", (f"{lo}-{hi}" if hi else lo)
    m = re.fullmatch(r"\^?\[A-Za-z0-9\]\{(\d+)(?:,(\d+))?\}\$?", pat)
    if m:
        lo, hi = m.group(1), m.group(2)
        return "Alphanumeric", (f"{lo}-{hi}" if hi else lo)
    m = re.fullmatch(r"\^?\[A-Z0-9\]\{(\d+)(?:,(\d+))?\}\$?", pat)
    if m:
        lo, hi = m.group(1), m.group(2)
        return "Alphanumeric", (f"{lo}-{hi}" if hi else lo)
    # fall back to the code-length range if the field is really a code list
    codes = field.get("codes") or {}
    if codes:
        lengths = {len(c) for c in codes}
        if len(lengths) == 1:
            return "Alphanumeric", str(next(iter(lengths)))
        return "Alphanumeric", f"{min(lengths)}-{max(lengths)}"
    return "Alphanumeric", ""  # blank = up to 255 chars


def _clear_values(ws) -> None:
    for r in range(2, _CLEAR_TO + 1):
        ws.cell(r, 5).value = None  # E: value
        ws.cell(r, 6).value = None  # F: description


def _write_dropdown(ws, codes: dict[str, str], description: Optional[str] = None) -> None:
    _clear_values(ws)
    if description is not None:
        ws["B2"] = description
    for i, (code, desc) in enumerate(codes.items(), start=2):
        ws.cell(i, 5).value = str(code)
        ws.cell(i, 6).value = str(desc) if desc else None


def _write_textfield(ws, char_type: str, req_char: str, description: Optional[str] = None) -> None:
    ws["E2"] = char_type
    ws["F2"] = req_char
    if description is not None:
        ws["B2"] = description


def generate(config_ref: str, output: str, name: Optional[str] = None,
             status: Optional[dict[str, str]] = None) -> Path:
    cfg = load_config(config_ref)
    wb = openpyxl.load_workbook(MASTER)

    # SETTINGS
    st = wb["SETTINGS"]
    st["A2"] = name or cfg.get("name") or "ISO 19650 naming standard"
    st["C2"] = DELIM_WORD.get(cfg.get("delimiter", "-"), "Hyphen")

    fields = cfg.get("fields", [])
    if len(fields) != len(NAMING_TABS):
        print(
            f"WARNING: config has {len(fields)} fields but the ACC template has "
            f"{len(NAMING_TABS)} naming tabs; mapping by position for the overlap.",
            file=sys.stderr,
        )

    for tab, field in zip(NAMING_TABS, fields):
        ws = wb[tab]
        desc = field.get("name")
        if (ws["C2"].value or "").strip().lower() == "drop-down list":
            codes = field.get("codes") or {}
            if not codes:
                print(f"WARNING: field '{field.get('key')}' -> {tab} is a drop-down "
                      f"but has no codes; leaving default.", file=sys.stderr)
                continue
            _write_dropdown(ws, codes, description=desc)
        else:  # text field
            ct, rc = parse_char(field)
            hint = desc
            codes = field.get("codes") or {}
            if codes:
                hint = f"{desc or tab} — code(s): {', '.join(codes)}"
            _write_textfield(ws, ct, rc, description=hint)

    # Status (drop-down, related attribute)
    status = status or cfg.get("statusCodes")
    if status:
        _write_dropdown(wb["Status"], status)

    # Revision (text field, related attribute) — from statusSuffix.revisionPattern if numeric-ish
    rev = wb["Revision"]
    revcodes = cfg.get("revisionCodes") or {}
    if revcodes:
        rev["B2"] = "e.g. " + ", ".join(list(revcodes)[:4])
    # keep the ISO default Alphanumeric 3-8 unless a tighter rule is derivable

    # Classification tab: left as the ACC Uniclass 2015 default.

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate an ACC naming-standard import .xlsx from a config.")
    ap.add_argument("config", help="naming config id (e.g. iso19650-uk or a <project-code>) or path to a .json")
    ap.add_argument("output", help="output .xlsx path")
    ap.add_argument("--name", default=None, help="naming standard name (SETTINGS A2)")
    ap.add_argument("--status", default=None,
                    help="override Status values as 'S0:desc,S1:desc,...' (order preserved)")
    args = ap.parse_args()
    status = None
    if args.status:
        status = {}
        for pair in args.status.split(","):
            code, _, desc = pair.partition(":")
            status[code.strip()] = desc.strip()
    path = generate(args.config, args.output, name=args.name, status=status)
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
