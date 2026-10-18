"""Smoke coverage for CLI telemetry result status."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from agent_wallet import openclaw_cli, telemetry  # noqa: E402


def _run_cli(tool: str, arguments_json: str) -> int:
    sys.argv = [
        "openclaw_cli",
        "invoke",
        "--user-id",
        "telemetry-test-user",
        "--tool",
        tool,
        "--arguments-json",
        arguments_json,
        "--config-json",
        '{"backend":"wdk_evm_local"}',
    ]
    return openclaw_cli.main()


def main() -> None:
    original_argv = sys.argv
    original_invoke = openclaw_cli._run_invoke
    original_record = telemetry.record
    original_bridge = os.environ.pop(telemetry.BRIDGE_ENV, None)
    events: list[dict] = []
    try:
        async def failed_invoke(*_args, **_kwargs):
            return {
                "tool": "swap_evm_tokens",
                "ok": False,
                "error": "approval_token is required to execute this swap",
            }

        openclaw_cli._run_invoke = failed_invoke
        telemetry.record = lambda tool, **kwargs: events.append({"tool": tool, **kwargs})

        # Adapter failures come back as a normal JSON result with exit code 0;
        # telemetry must still record the failure, its class and the mode.
        assert _run_cli("swap_evm_tokens", '{"mode":"execute","amount":"1"}') == 0
        assert events == [
            {
                "tool": "swap_evm_tokens",
                "backend": "wdk_evm_local",
                "ok": False,
                "mode": "execute",
                "error_class": "approval",
            }
        ]

        # Under the MCP bridge the bridge owns tool_invoke; the CLI stays quiet.
        events.clear()
        os.environ[telemetry.BRIDGE_ENV] = "1"
        assert _run_cli("swap_evm_tokens", '{"mode":"execute"}') == 0
        assert events == []
    finally:
        sys.argv = original_argv
        openclaw_cli._run_invoke = original_invoke
        telemetry.record = original_record
        if original_bridge is None:
            os.environ.pop(telemetry.BRIDGE_ENV, None)
        else:
            os.environ[telemetry.BRIDGE_ENV] = original_bridge

    print("smoke_telemetry_cli_result: ok")


if __name__ == "__main__":
    main()
