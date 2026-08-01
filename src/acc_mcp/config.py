"""Configuration: environment variables, endpoints, scopes, data directory."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loading (tiny, dependency-free). Search order:
#   1. $ACC_MCP_ENV_FILE   2. <package repo root>/.env   3. ~/.acc-mcp/.env
# Real environment variables always win over .env entries.
# ---------------------------------------------------------------------------

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # repo root (contains pyproject.toml)


def _load_env_file(path: Path) -> None:
    try:
        if not path.is_file():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def _init_env() -> None:
    custom = os.environ.get("ACC_MCP_ENV_FILE")
    if custom:
        _load_env_file(Path(custom).expanduser())
    _load_env_file(_PACKAGE_ROOT / ".env")
    _load_env_file(Path.home() / ".acc-mcp" / ".env")


_init_env()

# ---------------------------------------------------------------------------
# Core settings
# ---------------------------------------------------------------------------

CLIENT_ID: str = os.environ.get("APS_CLIENT_ID", "")
# Optional. Required only for a "Traditional Web App" (confidential client) APS app.
# Leave empty for a "Desktop, Mobile, Single-Page App" (public client, PKCE-only).
CLIENT_SECRET: str = os.environ.get("APS_CLIENT_SECRET", "")
CALLBACK_URL: str = os.environ.get("APS_CALLBACK_URL", "http://localhost:8087/callback")

# Default region for ACC Admin API routing: US | EMEA (others: AUS, CAN, DEU, GBR, IND, JPN)
DEFAULT_REGION: str = os.environ.get("APS_REGION", "US").upper()

# Auth preference: "3lo" (default — every call uses the signed-in user),
# "auto" (3-legged if logged in, else 2-legged), "2lo" (client credentials only;
# needs a client secret AND the app added as Custom Integration in the ACC hub).
AUTH_MODE: str = os.environ.get("ACC_AUTH_MODE", "3lo").lower()

# Optional: Autodesk user id (autodeskId / "sub") used for 2-legged impersonation
# headers when no 3-legged login has been performed.
FALLBACK_USER_ID: str = os.environ.get("ACC_USER_ID", "")

DATA_DIR: Path = Path(os.environ.get("ACC_MCP_DATA_DIR", str(Path.home() / ".acc-mcp"))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE: Path = DATA_DIR / "tokens.json"
DOWNLOAD_DIR: Path = Path(os.environ.get("ACC_MCP_DOWNLOAD_DIR", str(DATA_DIR / "downloads"))).expanduser()

# Extra directory with user-supplied naming-convention JSON configs
NAMING_DIR: Path = Path(os.environ.get("ACC_MCP_NAMING_DIR", str(DATA_DIR / "naming"))).expanduser()
# Neutral default so the server is project-agnostic. Set ACC_MCP_NAMING_CONFIG
# (env) to a project-specific config id, or pass config_id per tool call.
DEFAULT_NAMING_CONFIG: str = os.environ.get("ACC_MCP_NAMING_CONFIG", "iso19650-uk")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

APS_BASE = "https://developer.api.autodesk.com"
AUTH_TOKEN_URL = f"{APS_BASE}/authentication/v2/token"
AUTH_AUTHORIZE_URL = f"{APS_BASE}/authentication/v2/authorize"
USERINFO_URL = "https://api.aps.autodesk.com/userinfo"

ADMIN_V1 = f"{APS_BASE}/construction/admin/v1"
ADMIN_V2 = f"{APS_BASE}/construction/admin/v2"
PROJECT_V1 = f"{APS_BASE}/project/v1"
DATA_V1 = f"{APS_BASE}/data/v1"
DOCS_V1 = f"{APS_BASE}/bim360/docs/v1"
REVIEWS_V1 = f"{APS_BASE}/construction/reviews/v1"
FILES_V1 = f"{APS_BASE}/construction/files/v1"
OSS_V2 = f"{APS_BASE}/oss/v2"

# ---------------------------------------------------------------------------
# OAuth scopes
# ---------------------------------------------------------------------------

SCOPES_2LO = os.environ.get(
    "APS_SCOPES_2LO",
    "data:read data:write data:create data:search account:read account:write",
)
SCOPES_3LO = os.environ.get(
    "APS_SCOPES_3LO",
    "data:read data:write data:create data:search account:read account:write "
    "openid user-profile:read",
)
