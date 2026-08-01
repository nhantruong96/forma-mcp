# ACC MCP

MCP server that lets Claude manage an ISO 19650 / TCVN 14177 Common Data Environment on Autodesk Construction Cloud — projects, CDE folder trees, permissions, files, document status and naming compliance.

## Contents

- [Requirements](#requirements)
- [Step 1 — Install](#step-1--install)
- [Step 2 — Register with Claude Desktop](#step-2--register-with-claude-desktop)
- [Step 3 — First run](#step-3--first-run)
- [Using with Codex](#using-with-codex)
- [Testing with a standalone client](#testing-with-a-standalone-client)
- [Adding a new tool](#adding-a-new-tool)
- [Tool list](#tool-list)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [License](#license)

## Requirements

| Item | Required |
|---|---|
| OS | Windows 10/11 (macOS and Linux work; paths below are Windows) |
| Host app | Claude Desktop, or Codex CLI |
| Python | 3.10 or newer |
| uv | 0.5 or newer — `winget install astral-sh.uv` |
| Autodesk | An APS account with a Developer Hub, and an ACC hub where you are a project member |
| Git | Any recent version |

## Step 1 — Install

### 1.1 Create the APS app

1. Sign in at **https://aps.autodesk.com** with the Autodesk account you use for ACC.
2. If prompted that you have no hub: *Get an APS plan* → **View options** → pick the **Free** plan.
3. Create a Developer Hub if you do not have one: **https://manage.autodesk.com** → *Products and Services* → **Hubs** → **Create hub** → type **APS developer hub** → **Create and Activate**.
4. Back at aps.autodesk.com → profile menu → **My applications** → select your Developer Hub → **Create application**.
5. **Application type** — pick one:
   - **Traditional Web App** — has a Client Secret; fill in `APS_CLIENT_SECRET`.
   - **Desktop, Mobile, Single-Page App** — no secret; leave `APS_CLIENT_SECRET` empty (PKCE public client).
   - **Server-to-Server** — will not work; it has no 3-legged login.
6. **Callback URL:** enter exactly `http://localhost:8087/callback`.
7. **API Access:** tick **Data Management API**, **Autodesk Construction Cloud API**, **BIM 360 API**.
8. **Save changes**, then copy the **Client ID** (and **Client Secret** via *Show*).

### 1.2 Enable the Custom Integration in ACC

Required for every hub you want to reach, including in 3-legged mode.

1. Sign in at **acc.autodesk.com** as an **Account Admin** of the hub → **Account Admin**.
2. Left menu → **Custom Integrations** (some hubs: *Settings → Custom Integrations*) → **Add custom integration**.
3. Paste the **Client ID**, give it a display name, tick both **Account Administration** and **Document Management** → **Add**.
4. Make sure your Autodesk account is a member of at least one Docs project in that hub.
5. For hubs where you are not Account Admin, send the Client ID to that hub's Account Admin.

### 1.3 Clone and install

```powershell
git clone https://github.com/nhantruong96/acc-mcp.git C:\path\to\acc-mcp
cd C:\path\to\acc-mcp
uv sync
uv run python -m acc_mcp --selfcheck
```

Expected last line: `selfcheck OK`, with `tools registered 45`.

Re-run `uv sync` after every `git pull`.

Or run the script, which also creates `.env` and prints the Claude Desktop values:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Extras:

```powershell
uv sync --extra templates    # adds openpyxl, needed only by scripts\gen_acc_naming_template.py
```

### 1.4 Configure `.env`

```powershell
copy .env.example .env
```

Fill in `APS_CLIENT_ID` (and `APS_CLIENT_SECRET` if you chose Traditional Web App). Never commit `.env`; it is in `.gitignore`.

| Variable | Default | Meaning |
|---|---|---|
| `APS_CLIENT_ID` | (required) | Client ID of the APS app |
| `APS_CLIENT_SECRET` | (empty) | Fill in only for a Traditional Web App |
| `APS_CALLBACK_URL` | `http://localhost:8087/callback` | Must match the APS app exactly |
| `APS_REGION` | `US` | `US`, `EMEA`, `AUS`, `CAN`, `DEU`, `GBR`, `IND`, `JPN` |
| `ACC_AUTH_MODE` | `3lo` | `3lo` / `auto` / `2lo` |
| `ACC_MCP_NAMING_CONFIG` | `iso19650-uk` | Default naming convention id |
| `ACC_MCP_NAMING_DIR` | `~/.acc-mcp/naming` | Extra naming-convention JSON files |
| `ACC_MCP_DATA_DIR` | `~/.acc-mcp` | Token and download storage |
| `ACC_MCP_ENV_FILE` | (empty) | Alternate `.env` path |
| `ACC_USER_ID` | (empty) | 2-legged impersonation only |

Instead of `.env`, you can put the same variables in the `env` block of the Claude Desktop config in Step 2.

## Step 2 — Register with Claude Desktop

1. Open the config file:
   - Microsoft Store build: `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
   - Classic installer: `%APPDATA%\Claude\claude_desktop_config.json`
   - Or in the app: *Settings → Developer → Edit Config*.
2. Add the `acc` block inside `mcpServers`, keeping any other servers:

```json
{
  "mcpServers": {
    "acc": {
      "command": "C:\\path\\to\\acc-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "acc_mcp"],
      "env": {
        "APS_CLIENT_ID": "PASTE_CLIENT_ID_HERE",
        "APS_CLIENT_SECRET": "PASTE_SECRET_OR_LEAVE_EMPTY",
        "APS_CALLBACK_URL": "http://localhost:8087/callback",
        "APS_REGION": "US",
        "ACC_AUTH_MODE": "3lo",
        "ACC_MCP_NAMING_CONFIG": "iso19650-uk"
      }
    }
  }
}
```

3. Save, fully quit Claude Desktop (right-click the tray icon → **Quit**), then reopen it.
4. *Settings → Developer* must show **acc** as *running*.

## Step 3 — First run

In a new conversation, ask in plain language:

1. "Check the ACC authentication status" → `acc_auth_status` — expect `app_configured: true`, `auth_mode: 3lo`.
2. "Sign me in to Autodesk" → `acc_login` — a browser opens; sign in and click **Allow**.
3. "Which hubs and projects can I see?" → `acc_whoami` → `acc_list_hubs` → `acc_list_projects`.

Set up a CDE for one project, in this order:

1. "Create an ACC project named `[name]`, type `[Street/Bridge/…]`, job number `[no.]`, timezone Asia/Bangkok" → `acc_create_project`.
2. "Check whether the project products are active yet" → `acc_get_project` — wait for `docs: active`.
3. "Add `[email]` to Docs and make me project admin" → `acc_add_project_user`.
4. "In Project Files, build the CDE tree with disciplines `[…]` and milestones `[…]`" → `acc_cde_structure_template` → `acc_create_folder_structure`.
5. "Design team uploads to WIP; client gets view/download on PUBLISHED; ARCHIVED is manage-only" → `acc_set_folder_permissions`.
6. "Audit the naming of everything under SHARED" → `acc_audit_folder_naming`.
7. "Set status S4 on the files just approved" → `acc_set_custom_attributes`.

Conventions:

- Container ID fields, in order: `Project-Originator-Volume-Level-Type-Role-Number`, separator `-`.
- Status codes `S0`–`S5`, `A1`…; revision codes alphanumeric, 3–8 characters (e.g. `P01`, `C01`).
- Data Management ids carry a `b.` prefix; every tool accepts the id with or without it.
- Region defaults to `US`; pass `region` per call or set `APS_REGION` for EMEA hubs.
- Sessions last ~15 days of inactivity, then run `acc_login` again.
- Two actions must be done in the ACC web UI: turning on **File naming standard** (Docs → Files → Settings), and clicking **approve/reject** in Reviews.

Generate an ACC naming-standard workbook to import:

```powershell
uv sync --extra templates
uv run python scripts\gen_acc_naming_template.py iso19650-uk out.xlsx --name "Project naming standard"
```

Then in ACC Docs → *Settings* → **File naming standard** → **Import** → pick `out.xlsx`.

## Using with Codex

1. Install the server as in [Step 1 — Install](#step-1--install).
2. Open `%USERPROFILE%\.codex\config.toml`.
3. Add:

```toml
[mcp_servers.acc]
command = "C:\\path\\to\\acc-mcp\\.venv\\Scripts\\python.exe"
args = ["-m", "acc_mcp"]

[mcp_servers.acc.env]
APS_CLIENT_ID = "PASTE_CLIENT_ID_HERE"
APS_CLIENT_SECRET = "PASTE_SECRET_OR_LEAVE_EMPTY"
APS_CALLBACK_URL = "http://localhost:8087/callback"
APS_REGION = "US"
ACC_AUTH_MODE = "3lo"
ACC_MCP_NAMING_CONFIG = "iso19650-uk"
```

4. Restart the Codex CLI.
5. Run `/mcp` in Codex to confirm `acc` is listed.

## Testing with a standalone client

```powershell
uv run python -m acc_mcp --selfcheck
uv run acc-mcp --version
uv run acc-mcp --list-tools
npx @modelcontextprotocol/inspector uv run acc-mcp
```

## Adding a new tool

1. Open the matching module in `src/acc_mcp/tools/` (or create one).
2. Define the function and decorate it:

```python
from ..app import mcp
from .common import api_get

@mcp.tool()
async def acc_my_tool(project_id: str) -> dict:
    """One-line summary shown to the model. Then the argument details."""
    return await api_get(f"/project/v1/projects/{project_id}")
```

3. If you created a new module, add it to `src/acc_mcp/tools/__init__.py`.
4. Never use `print()` — stdout is the JSON-RPC channel.
5. Verify:

```powershell
uv run python -m acc_mcp --selfcheck
uv run acc-mcp --list-tools
```

6. Fully quit and reopen Claude Desktop.

## Tool list

| Group | Tools |
|---|---|
| Authentication | `acc_auth_status`, `acc_login`, `acc_logout`, `acc_whoami` |
| Hubs & projects | `acc_list_hubs`, `acc_list_projects`, `acc_get_project`, `acc_create_project` |
| Members | `acc_list_project_users`, `acc_add_project_user`, `acc_update_project_user`, `acc_remove_project_user`, `acc_import_project_users`, `acc_list_companies` |
| CDE folders | `acc_get_top_folders`, `acc_list_folder`, `acc_create_folder`, `acc_create_folder_structure`, `acc_rename_folder`, `acc_move_folder`, `acc_delete_folder`, `acc_restore_folder`, `acc_search_folder` |
| Permissions | `acc_get_folder_permissions`, `acc_set_folder_permissions`, `acc_remove_folder_permissions` |
| Files | `acc_get_item`, `acc_list_item_versions`, `acc_upload_file`, `acc_download_file`, `acc_copy_file` |
| Status & metadata | `acc_list_custom_attributes`, `acc_create_custom_attribute`, `acc_get_docs_metadata`, `acc_set_custom_attributes`, `acc_get_naming_standard` |
| ISO 19650 naming | `acc_naming_configs`, `acc_validate_names`, `acc_compose_name`, `acc_audit_folder_naming`, `acc_cde_structure_template` |
| Reviews | `acc_list_review_workflows`, `acc_list_reviews`, `acc_get_review`, `acc_create_review` |

Add a project-specific naming convention: copy `src/acc_mcp/naming_configs/iso19650-uk.json` to `%USERPROFILE%\.acc-mcp\naming\[project-code].json`, edit the code tables, then pass `config_id="[project-code]"` or set `ACC_MCP_NAMING_CONFIG`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | `mcp` 2.0 removed that module. The pin is `mcp>=1.10.0,<2` in `pyproject.toml` and `requirements.txt` — run `uv lock` then `uv sync`. |
| `The client credentials are invalid`, or 400/401 on token | Wrong Client ID; app not attached to a Developer Hub (Step 1.1); or app-type mismatch — Traditional Web App **needs** the secret, Desktop/Mobile/SPA **needs it empty**. |
| `acc_whoami` / `acc_list_hubs` returns an empty hub list | App not added to the hub's **Custom Integrations** (Step 1.2), and Document Management not ticked. Or your account is not a member of any Docs project in that hub. |
| 403 on an operation | Your Autodesk account lacks the right: Account Admin to create projects, CONTROL/Manage for folder permissions, Project Admin to edit members. |
| 404 on Admin calls (list/create project) | Wrong region — try `APS_REGION=EMEA`. |
| `Cannot open local port 8087` during login | Port taken. Change the port in `APS_CALLBACK_URL` **and** in the APS app, then restart the host. |
| Login reports a timeout | Ask Claude to run `acc_login` with `wait_only=true`, or log in again. |
| `refresh token is no longer valid` | Unused for ~15 days — run `acc_login` again. |
| Server missing in Claude Desktop | Bad JSON (comma, `\\` escapes), or a wrong `python.exe` path. Check `mcp-server-acc.log` under `logs\` next to the config file. |
| New project stuck at `activating` | Normal for a few minutes. `activationFailed` → check the hub's product licences and recreate. |
| Cannot move a file between folders | Not supported by the API — use `acc_copy_file`. Folders can be moved with `acc_move_folder`. |
| Naming standard changes do not apply | The API is read-only for naming standards. Import the workbook in ACC Docs, then confirm with `acc_get_naming_standard`. |
| Reviews cannot be approved from Claude | Approve/reject only exists in the ACC UI. `acc_create_review` / `acc_get_review` start and monitor. |

## Repository layout

```
src/acc_mcp/
  server.py          entry point (--version, --selfcheck, --list-tools)
  __main__.py        python -m acc_mcp
  app.py             FastMCP instance
  config.py          environment variables, endpoints, scopes
  auth.py            3-legged OAuth + PKCE, token storage
  http.py            APS HTTP client
  ids.py             'b.' prefix handling
  naming.py          ISO 19650 name validation / composition
  naming_configs/    bundled naming conventions (iso19650-uk.json)
  tools/             the 45 MCP tools, grouped by area
scripts/
  install.ps1                  uv sync + selfcheck
  gen_acc_naming_template.py   naming config JSON -> ACC import .xlsx
naming_templates/
  Default_ISO_19650_master.xlsx   Autodesk master workbook used as the mould
pyproject.toml       dependencies and console script
uv.lock              committed on purpose so `uv sync` is reproducible
.env.example         template for .env (never commit .env)
```

## License

MIT — see [LICENSE](LICENSE).
