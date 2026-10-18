"""The MCP bridge records every tool call under its public name, exactly once."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEX_SERVER = REPO_ROOT / "codex" / "plugins" / "agent-wallet" / "server.py"
ENV_KEYS = (
    "OPENCLAW_HOME",
    "AGENT_WALLET_PACKAGE_ROOT",
    "AGENT_WALLET_HOST",
    "AGENT_WALLET_NO_TELEMETRY",
    "AGENT_WALLET_TELEMETRY_BRIDGE",
)


def _load_codex_server():
    spec = importlib.util.spec_from_file_location("telemetry_bridge_codex_server", CODEX_SERVER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _ApprovalError(RuntimeError):
    code = "approval_required"


def main() -> None:
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        with tempfile.TemporaryDirectory(prefix="agentlayer-telemetry-bridge-") as tmp:
            home = Path(tmp)
            os.environ["OPENCLAW_HOME"] = str(home)
            os.environ["AGENT_WALLET_PACKAGE_ROOT"] = str(REPO_ROOT / "agent-wallet")
            os.environ["AGENT_WALLET_HOST"] = "cursor"
            os.environ.pop("AGENT_WALLET_NO_TELEMETRY", None)
            os.environ.pop("AGENT_WALLET_TELEMETRY_BRIDGE", None)
            # Keep the throttle closed so no detached flush claims the spool mid-test.
            (home / "telemetry_last_flush").write_text(str(time.time() + 3600), encoding="utf-8")

            server = _load_codex_server()
            assert server._bridge_telemetry() is not None
            # Subprocesses are told the bridge owns tool_invoke recording.
            assert server._cli_env(home)["AGENT_WALLET_TELEMETRY_BRIDGE"] == "1"

            address = "0x1111111111111111111111111111111111111111"
            server._record_bridge_session_start()
            server._record_bridge_tool_call("get_wallet_overview", {"address": address}, None)
            server._record_bridge_tool_call(
                "swap_evm_tokens",
                {"mode": "execute", "backend": "evm", "amount": "12.5", "recipient": address},
                _ApprovalError("approval_token missing for this swap"),
            )

            spool = (home / "telemetry_spool.jsonl").read_text(encoding="utf-8")
            assert address not in spool and "12.5" not in spool
            events = [json.loads(line) for line in spool.splitlines() if line.strip()]
            assert [(e["event"], e["tool"]) for e in events] == [
                ("session_start", ""),
                ("tool_invoke", "get_wallet_overview"),
                ("tool_invoke", "swap_evm_tokens"),
            ]
            assert all(e["host"] == "cursor" for e in events)
            overview, swap = events[1], events[2]
            assert overview["ok"] is True and "mode" not in overview and overview["error_class"] == ""
            assert swap["ok"] is False
            assert swap["mode"] == "execute"
            assert swap["error_class"] == "approval"
            assert swap["backend"] == "wdk_evm_local"

            # Opt-out still means zero footprint, even from the bridge.
            os.environ["AGENT_WALLET_NO_TELEMETRY"] = "1"
            server._record_bridge_tool_call("get_wallet_balance", {}, None)
            assert (home / "telemetry_spool.jsonl").read_text(encoding="utf-8") == spool
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("smoke_telemetry_bridge_recording: ok")


if __name__ == "__main__":
    main()
