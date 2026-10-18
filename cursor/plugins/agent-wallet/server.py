"""Cursor entry point for the shared AgentLayer wallet MCP runtime."""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path


def main() -> None:
    openclaw_home = Path(os.environ.get("OPENCLAW_HOME", "~/.openclaw")).expanduser()
    package_root = Path(
        os.environ.get("AGENT_WALLET_PACKAGE_ROOT")
        or os.environ.get("OPENCLAW_AGENT_WALLET_PACKAGE_ROOT")
        or openclaw_home / "agent-wallet-runtime" / "current" / "agent-wallet"
    ).expanduser()
    shared_server = package_root.parent / "mcp" / "server.py"
    if not shared_server.is_file():
        print(
            json.dumps({
                "error": "AgentLayer shared MCP server is missing from the wallet runtime.",
                "fix": "Run: wallet install --yes",
            }),
            file=sys.stderr,
        )
        raise SystemExit(1)
    runpy.run_path(str(shared_server), run_name="__main__")


if __name__ == "__main__":
    main()
