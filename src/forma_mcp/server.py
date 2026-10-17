"""Entry point: run the Forma MCP server over stdio (for Claude Desktop / Code)."""

from __future__ import annotations

import sys

from .app import mcp
from . import tools  # noqa: F401  (importing registers all tools)


def _selfcheck() -> int:
    """Verify the installed environment can actually serve tools. Returns an exit code."""
    import anyio

    from . import __version__
    from . import config
    from .naming import list_configs, load_config

    problems: list[str] = []

    async def _collect() -> list[str]:
        return [t.name for t in await mcp.list_tools()]

    tool_names = anyio.run(_collect)
    if not tool_names:
        problems.append("no tools registered")

    config_ids = sorted(c["id"] for c in list_configs())
    if config.DEFAULT_NAMING_CONFIG not in config_ids:
        problems.append(f"default naming config {config.DEFAULT_NAMING_CONFIG!r} not found")
    for cid in config_ids:
        try:
            load_config(cid)
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            problems.append(f"naming config {cid!r} failed to load: {exc}")

    print(f"forma-mcp {__version__}")
    print(f"python           {sys.version.split()[0]}")
    print(f"tools registered {len(tool_names)}")
    print(f"naming configs   {len(config_ids)} ({', '.join(config_ids) or 'none'})")
    print(f"data dir         {config.DATA_DIR}")
    print(f"auth mode        {config.AUTH_MODE}")
    print(f"app configured   {bool(config.CLIENT_ID)}")

    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print("selfcheck OK")
    return 0


def main() -> None:
    if "--version" in sys.argv:
        from . import __version__

        print(f"forma-mcp {__version__}")
        return
    if "--selfcheck" in sys.argv:
        raise SystemExit(_selfcheck())
    if "--list-tools" in sys.argv:
        import anyio

        async def _list() -> None:
            for t in await mcp.list_tools():
                print(f"{t.name}: {(t.description or '').strip().splitlines()[0]}")

        anyio.run(_list)
        return
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
