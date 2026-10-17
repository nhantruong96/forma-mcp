"""FastMCP application instance (imported by every tool module)."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "forma_mcp",
    instructions=(
        "Tools for managing a Common Data Environment (CDE) on Autodesk Construction "
        "Cloud (ACC) per ISO 19650 / TCVN 14177: hubs & projects, folder structures, "
        "folder permissions, file upload/download, document status & custom attributes, "
        "container-ID naming validation and approval-workflow (reviews) visibility; plus read/download access to the hub's Autodesk Content Catalog (catalog_* tools). "
        "Authentication is 3-legged by default: run acc_auth_status first, and if no "
        "user session exists, sign in with acc_login — every action then runs under "
        "the signed-in user's own ACC permissions and identity (audit trail). "
        "IDs: Data Management ids carry a 'b.' prefix; tools accept either form.\n\n"
        "DEFAULT CDE WORKING RULES — apply to every action on the CDE unless the user\n"
        "explicitly instructs otherwise:\n"
        "1. NEVER delete a file or a folder. Nothing on a CDE is removed: superseded\n"
        "   containers are moved to the Archived area, never deleted. Do not call\n"
        "   acc_delete_folder on your own initiative — ask the user first.\n"
        "2. Files are only ever OVERWRITTEN: upload with the SAME name so ACC adds a new\n"
        "   version and the lineage / audit trail is preserved. Never upload under a\n"
        "   different name to 'replace' a document — that creates a separate item and\n"
        "   loses its version history.\n"
        "3. RENAME (file or folder) only when the user asks for it specifically. File\n"
        "   names are the information-container IDs and normally never change.\n"
        "4. The state of a document is changed through METADATA, not through its name:\n"
        "   Status (suitability), Revision, Classification, Description — see\n"
        "   acc_set_custom_attributes. Status/Revision/Classification are native ACC\n"
        "   attributes of the file naming standard; create a custom attribute only when\n"
        "   ACC has no equivalent.\n"
        "5. Upload to the CDE only what has actually been shared/issued; keep work in\n"
        "   progress (P0x.0y) outside the CDE until it is shared."
    ),
)
