"""FastMCP application instance (imported by every tool module)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "acc_mcp",
    instructions=(
        "Tools for managing a Common Data Environment (CDE) on Autodesk Construction "
        "Cloud (ACC) per ISO 19650 / TCVN 14177: hubs & projects, folder structures, "
        "folder permissions, file upload/download, document status & custom attributes, "
        "container-ID naming validation and approval-workflow (reviews) visibility. "
        "Authentication is 3-legged by default: run acc_auth_status first, and if no "
        "user session exists, sign in with acc_login — every action then runs under "
        "the signed-in user's own ACC permissions and identity (audit trail). "
        "IDs: Data Management ids carry a 'b.' prefix; tools accept either form."
    ),
)
