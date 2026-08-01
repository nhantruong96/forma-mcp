"""Hub / project / member administration tools (ACC Admin API + DM hubs)."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from pydantic import Field

from ..app import mcp
from ..http import ok, request, tool_error
from ..ids import without_b
from .. import config
from .common import (
    admin_region,
    find_project_user,
    fmt_products,
    fmt_project_admin,
    parse_products_spec,
    set_region_hint,
)


@mcp.tool(
    name="acc_list_hubs",
    annotations={"title": "ACC: List hubs (accounts)", "readOnlyHint": True},
)
async def acc_list_hubs() -> str:
    """List the ACC/BIM 360 hubs (accounts) visible to the current credentials,
    with their account ids and data region. The accountId (without 'b.') is
    what the project-administration tools need."""
    try:
        payload = await request("GET", f"{config.PROJECT_V1}/hubs", impersonate_header="x-user-id")
        hubs = []
        for h in payload.get("data", []):
            attrs = h.get("attributes", {})
            region = ((attrs.get("extension") or {}).get("data") or {}).get("region")
            acct = h.get("id", "").removeprefix("b.")
            if region:
                set_region_hint(acct, "EMEA" if region == "EMEA" else region)
            hubs.append(
                {
                    "accountId": acct,
                    "hubId": h.get("id"),
                    "name": attrs.get("name"),
                    "region": region,
                    "type": (attrs.get("extension") or {}).get("type"),
                }
            )
        return ok(hubs, count=len(hubs))
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_projects",
    annotations={"title": "ACC: List projects", "readOnlyHint": True},
)
async def acc_list_projects(
    account_id: Annotated[str, Field(description="Account/hub id (with or without the 'b.' prefix)")],
    name_contains: Annotated[Optional[str], Field(description="Filter: project name contains this text")] = None,
    classification: Annotated[Optional[str], Field(description="Filter: production | template | component | sample")] = None,
    status: Annotated[Optional[str], Field(description="Filter: active | pending | archived | suspended")] = None,
    platform: Annotated[Optional[str], Field(description="Filter: acc | bim360")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> str:
    """List projects of an ACC account (hub), newest API, with filters and
    pagination. Also use this with classification='template' to discover
    project templates for acc_create_project."""
    try:
        acct = without_b(account_id)
        region = await admin_region(acct)
        params: dict = {"limit": limit, "offset": offset, "sort": "name"}
        if name_contains:
            params["filter[name]"] = name_contains
            params["filterTextMatch"] = "contains"
        if classification:
            params["filter[classification]"] = classification
        if status:
            params["filter[status]"] = status
        if platform:
            params["filter[platform]"] = platform
        payload = await request(
            "GET",
            f"{config.ADMIN_V1}/accounts/{acct}/projects",
            params=params,
            region=region,
            impersonate_header="User-Id",
        )
        pag = payload.get("pagination", {})
        return ok(
            [fmt_project_admin(p) for p in payload.get("results", [])],
            totalResults=pag.get("totalResults"),
            offset=offset,
            region=region,
        )
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_get_project",
    annotations={"title": "ACC: Get project", "readOnlyHint": True},
)
async def acc_get_project(
    project_id: Annotated[str, Field(description="Project id (bare GUID or 'b.'-prefixed)")],
    account_id: Annotated[Optional[str], Field(description="Account id — speeds up region detection")] = None,
) -> str:
    """Get one project's details including per-product activation status
    (docs, designCollaboration, ...). Use after acc_create_project to check
    that product activation finished (statuses become 'active')."""
    try:
        pid = without_b(project_id)
        region = await admin_region(account_id) if account_id else None
        payload = await request(
            "GET",
            f"{config.ADMIN_V1}/projects/{pid}",
            region=region or config.DEFAULT_REGION,
            impersonate_header="User-Id",
        )
        out = fmt_project_admin(payload)
        out["accountId"] = payload.get("accountId")
        out["products"] = fmt_products(payload)
        out["timezone"] = payload.get("timezone")
        out["projectValue"] = payload.get("projectValue")
        return ok(out)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_create_project",
    annotations={"title": "ACC: Create project", "readOnlyHint": False, "destructiveHint": False},
)
async def acc_create_project(
    account_id: Annotated[str, Field(description="Account/hub id")],
    name: Annotated[str, Field(min_length=1, max_length=255, description="Project name (must be unique in the account)")],
    project_type: Annotated[str, Field(description="Project type, e.g. 'Bridge', 'Seaport', 'Office'. Use 'Template Project' when creating a template")] = "Bridge",
    template_project_id: Annotated[Optional[str], Field(description="Clone products/settings/folder structure from this template project id")] = None,
    classification: Annotated[str, Field(description="production | template | component | sample")] = "production",
    start_date: Annotated[Optional[str], Field(description="ISO date YYYY-MM-DD")] = None,
    end_date: Annotated[Optional[str], Field(description="ISO date YYYY-MM-DD")] = None,
    job_number: Annotated[Optional[str], Field(description="Internal job/contract number")] = None,
    timezone: Annotated[Optional[str], Field(description="IANA timezone, e.g. 'Asia/Bangkok' (UTC+7)")] = "Asia/Bangkok",
    currency: Annotated[Optional[str], Field(description="ISO 4217 currency for projectValue, e.g. 'VND', 'USD'")] = None,
    project_value: Annotated[Optional[float], Field(description="Estimated project value in the given currency")] = None,
    latitude: Annotated[Optional[float], Field(description="Site latitude in decimal degrees, e.g. 12.294595 (must be paired with longitude)", ge=-90, le=90)] = None,
    longitude: Annotated[Optional[float], Field(description="Site longitude in decimal degrees, e.g. 109.177071 (must be paired with latitude)", ge=-180, le=180)] = None,
    address_line1: Annotated[Optional[str], Field(description="Street address line 1")] = None,
    address_line2: Annotated[Optional[str], Field(description="Street address line 2")] = None,
    city: Annotated[Optional[str], Field(description="City, e.g. 'Nha Trang'")] = None,
    state_or_province: Annotated[Optional[str], Field(description="State/province, e.g. 'Khanh Hoa'")] = None,
    postal_code: Annotated[Optional[str], Field(description="Postal / ZIP code")] = None,
    country: Annotated[Optional[str], Field(description="ISO 3166-1 alpha-2 country code, e.g. 'VN'")] = None,
) -> str:
    """Create an ACC project (optionally cloned from a template) — the first
    step of setting up a CDE. The signed-in user (acc_login) must be an
    account admin of the hub. Product activation is ASYNCHRONOUS: poll
    acc_get_project until products show 'active', then add a project admin
    with acc_add_project_user (this also triggers member copy from the
    template).

    Location (latitude/longitude, address) can ONLY be set here at creation —
    the ACC Admin API has no project-update endpoint, so an existing project's
    location must be changed in the ACC UI (Project Admin > Settings)."""
    try:
        acct = without_b(account_id)
        region = await admin_region(acct)
        body: dict = {"name": name, "type": project_type, "classification": classification}
        if template_project_id:
            body["template"] = {
                "projectId": without_b(template_project_id),
                "options": {"field": {"includeCompanies": True, "includeLocations": True}},
            }
        if start_date:
            body["startDate"] = start_date
        if end_date:
            body["endDate"] = end_date
        if job_number:
            body["jobNumber"] = job_number
        if timezone:
            body["timezone"] = timezone
        if currency and project_value is not None:
            body["projectValue"] = {"value": project_value, "currency": currency}
        if (latitude is None) != (longitude is None):
            return "Error: provide BOTH latitude and longitude, or neither."
        if latitude is not None and longitude is not None:
            body["latitude"] = latitude
            body["longitude"] = longitude
        for key, val in (
            ("addressLine1", address_line1),
            ("addressLine2", address_line2),
            ("city", city),
            ("stateOrProvince", state_or_province),
            ("postalCode", postal_code),
            ("country", country),
        ):
            if val:
                body[key] = val
        payload = await request(
            "POST",
            f"{config.ADMIN_V1}/accounts/{acct}/projects",
            json_body=body,
            region=region,
            impersonate_header="User-Id",
            auth="auto",
        )
        out = fmt_project_admin(payload)
        out["products"] = fmt_products(payload)
        return ok(
            out,
            note=(
                "Project created (202): products are activating in the background. "
                "Poll acc_get_project until all products are 'active'. If cloned from a "
                "template, add one project admin to trigger the template member copy. "
                "NOTE: the ACC file-naming standard is NOT copied from templates — "
                "configure it in the project's Docs settings UI."
            ),
        )
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_project_users",
    annotations={"title": "ACC: List project members", "readOnlyHint": True},
)
async def acc_list_project_users(
    project_id: Annotated[str, Field(description="Project id")],
    email: Annotated[Optional[str], Field(description="Filter by email (contains)")] = None,
    name_contains: Annotated[Optional[str], Field(description="Filter by user name")] = None,
    company_name: Annotated[Optional[str], Field(description="Filter by company name")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> str:
    """List members of a project with their company, roles, access levels and
    product access. The returned 'id' (ACC user GUID) and 'autodeskId' are the
    subject ids used by the folder-permission tools; 'roleIds'/'roles' reveal
    the project's role ids for ROLE-based permissions."""
    try:
        params: dict = {"limit": limit, "offset": offset}
        if email:
            params["filter[email]"] = email
        if name_contains:
            params["filter[name]"] = name_contains
        if company_name:
            params["filter[companyName]"] = company_name
        if email or name_contains or company_name:
            params["filterTextMatch"] = "contains"
        payload = await request(
            "GET",
            f"{config.ADMIN_V1}/projects/{without_b(project_id)}/users",
            params=params,
        )
        users = [
            {
                "id": u.get("id"),
                "autodeskId": u.get("autodeskId"),
                "name": u.get("name"),
                "email": u.get("email"),
                "companyName": u.get("companyName"),
                "companyId": u.get("companyId"),
                "roles": [{"id": r.get("id"), "name": r.get("name")} for r in (u.get("roles") or [])],
                "accessLevels": u.get("accessLevels"),
                "products": [
                    {"key": p.get("key"), "access": p.get("access")}
                    for p in (u.get("products") or [])
                    if p.get("access") not in (None, "none")
                ],
                "status": u.get("status"),
            }
            for u in payload.get("results", [])
        ]
        pag = payload.get("pagination", {})
        return ok(users, totalResults=pag.get("totalResults"), offset=offset)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_add_project_user",
    annotations={"title": "ACC: Add project member", "readOnlyHint": False},
)
async def acc_add_project_user(
    project_id: Annotated[str, Field(description="Project id")],
    email: Annotated[str, Field(description="Email of the user to add (invited if not yet an account member)")],
    company_id: Annotated[Optional[str], Field(description="Company id (see acc_list_companies)")] = None,
    role_ids: Annotated[Optional[str], Field(description="Comma-separated role ids (find via acc_list_project_users of a reference project)")] = None,
    products: Annotated[str, Field(description="Product access as key:access pairs, comma-separated. Access = member|administrator|none. Example: 'docs:member' or 'docs:administrator,projectAdministration:administrator' for a project admin")] = "docs:member",
    make_project_admin: Annotated[bool, Field(description="Shortcut: grant administrator on projectAdministration AND all products listed")] = False,
) -> str:
    """Add one member to a project with product access, company and roles.
    The signed-in user must be a project admin (or account admin). Rule:
    projectAdministration:administrator requires every other granted product
    to be administrator too."""
    try:
        prods = parse_products_spec(products) or []
        if make_project_admin:
            keys = {p["key"] for p in prods}
            keys.update({"projectAdministration", "docs"})
            prods = [{"key": k, "access": "administrator"} for k in sorted(keys)]
        body: dict = {"email": email, "products": prods}
        if company_id:
            body["companyId"] = company_id
        if role_ids:
            body["roleIds"] = [r.strip() for r in role_ids.split(",") if r.strip()]
        payload = await request(
            "POST",
            f"{config.ADMIN_V1}/projects/{without_b(project_id)}/users",
            json_body=body,
            impersonate_header="User-Id",
        )
        return ok({"id": payload.get("id"), "email": payload.get("email"), "status": payload.get("status")})
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_update_project_user",
    annotations={"title": "ACC: Update project member", "readOnlyHint": False},
)
async def acc_update_project_user(
    project_id: Annotated[str, Field(description="Project id")],
    user: Annotated[str, Field(description="Project user id, autodeskId, or email")],
    company_id: Annotated[Optional[str], Field(description="New company id")] = None,
    role_ids: Annotated[Optional[str], Field(description="Comma-separated role ids (REPLACES existing roles)")] = None,
    products: Annotated[Optional[str], Field(description="Product access changes as key:access pairs, e.g. 'docs:administrator' or 'build:none'")] = None,
) -> str:
    """Change a project member's company, roles or product access. Accepts an
    email and resolves it to the user id automatically."""
    try:
        pid = without_b(project_id)
        uid = user
        if "@" in user:
            found = await find_project_user(pid, user)
            if not found:
                return f"Error: no member with email '{user}' in project {pid}."
            uid = found["id"]
        body: dict = {}
        if company_id:
            body["companyId"] = company_id
        if role_ids is not None:
            body["roleIds"] = [r.strip() for r in role_ids.split(",") if r.strip()]
        prods = parse_products_spec(products)
        if prods:
            body["products"] = prods
        if not body:
            return "Error: nothing to update — provide company_id, role_ids or products."
        payload = await request(
            "PATCH",
            f"{config.ADMIN_V1}/projects/{pid}/users/{uid}",
            json_body=body,
            impersonate_header="User-Id",
        )
        return ok(payload)
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_remove_project_user",
    annotations={"title": "ACC: Remove project member", "readOnlyHint": False, "destructiveHint": True},
)
async def acc_remove_project_user(
    project_id: Annotated[str, Field(description="Project id")],
    user: Annotated[str, Field(description="Project user id, autodeskId, or email")],
) -> str:
    """Remove a member from a project (their data/history stays; access is
    revoked). Confirm with the user before calling."""
    try:
        pid = without_b(project_id)
        uid = user
        if "@" in user:
            found = await find_project_user(pid, user)
            if not found:
                return f"Error: no member with email '{user}' in project {pid}."
            uid = found["id"]
        await request(
            "DELETE",
            f"{config.ADMIN_V1}/projects/{pid}/users/{uid}",
            impersonate_header="User-Id",
        )
        return ok({"removed": uid})
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_import_project_users",
    annotations={"title": "ACC: Bulk add project members", "readOnlyHint": False},
)
async def acc_import_project_users(
    project_id: Annotated[str, Field(description="Project id")],
    users_json: Annotated[str, Field(description=(
        "JSON array of users, each: {\"email\": \"...\", \"companyId\"?: \"...\", "
        "\"roleIds\"?: [..], \"products\"?: [{\"key\": \"docs\", \"access\": \"member\"}]}. "
        "Max 200 per call. Products default to docs:member if omitted"
    ))],
) -> str:
    """Bulk-add up to 200 members to a project in one call (asynchronous server
    job). Good for mobilizing a full project team from a list."""
    try:
        users = json.loads(users_json)
        if not isinstance(users, list) or not users:
            return "Error: users_json must be a non-empty JSON array."
        for u in users:
            if "email" not in u:
                return "Error: every user needs an 'email'."
            u.setdefault("products", [{"key": "docs", "access": "member"}])
        payload = await request(
            "POST",
            f"{config.ADMIN_V2}/projects/{without_b(project_id)}/users:import",
            json_body={"users": users},
            impersonate_header="User-Id",
        )
        return ok(payload, note="Import accepted (async). Verify with acc_list_project_users shortly.")
    except json.JSONDecodeError as exc:
        return f"Error: users_json is not valid JSON: {exc}"
    except Exception as exc:
        return tool_error(exc)


@mcp.tool(
    name="acc_list_companies",
    annotations={"title": "ACC: List companies", "readOnlyHint": True},
)
async def acc_list_companies(
    account_id: Annotated[str, Field(description="Account/hub id")],
    name_contains: Annotated[Optional[str], Field(description="Filter by company name")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> str:
    """List companies (partners) registered in the account — source of the
    companyId used when adding members and granting COMPANY folder permissions."""
    try:
        acct = without_b(account_id)
        region = await admin_region(acct)
        params: dict = {"limit": limit, "offset": offset}
        if name_contains:
            params["filter[name]"] = name_contains
            params["filterTextMatch"] = "contains"
        payload = await request(
            "GET",
            f"{config.ADMIN_V1}/accounts/{acct}/companies",
            params=params,
            region=region,
        )
        companies = [
            {"id": c.get("id"), "name": c.get("name"), "trade": c.get("trade"), "status": c.get("status")}
            for c in payload.get("results", [])
        ]
        pag = payload.get("pagination", {})
        return ok(companies, totalResults=pag.get("totalResults"), offset=offset)
    except Exception as exc:
        return tool_error(exc)
