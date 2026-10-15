"""Smoke tests for the automatic Morpho scan attached to ordinary read-only wallet calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _secret_test_utils import install_test_sealed_secrets  # noqa: E402
from agent_wallet import morpho_monitor  # noqa: E402
from agent_wallet.openclaw_adapter import OpenClawWalletAdapter  # noqa: E402
from agent_wallet.wallet_layer.base import AgentWalletBackend, WalletBackendError, WalletCapabilities  # noqa: E402

OWNER = "0x1111111111111111111111111111111111111111"


class FakeScanBackend(AgentWalletBackend):
    name = "wdk_evm_local"
    chain = "evm"
    network = "base"
    sign_only = False

    def __init__(self) -> None:
        self.scan_calls: list[dict[str, Any]] = []
        self.fail = False
        self.delay = 0.0

    def with_network(self, network: str) -> "FakeScanBackend":
        return self

    async def get_address(self) -> str | None:
        return OWNER

    async def get_balance(self, address: str | None = None) -> dict:
        return {"chain": "evm", "network": self.network, "address": OWNER, "balance_wei": "1"}

    async def get_morpho_rebalance_report(self, *, include_migration_cost: bool = True, **_: Any) -> dict:
        self.scan_calls.append({"include_migration_cost": include_migration_cost})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise WalletBackendError("Morpho API request failed")
        return {
            "address": OWNER,
            "scanned_at": "2026-10-15T12:00:00.000Z",
            "position_count": 1,
            "opportunity_count": 1,
            "network_reports": [
                {
                    "network": "base",
                    "chainId": 8453,
                    "error": None,
                    "positions": [
                        {
                            "network": "base",
                            "chainId": 8453,
                            "current": {
                                "vault": {"address": "0xA1", "name": "Current USDC"},
                                "asset": {"symbol": "USDC"},
                                "assetsUsd": 1000,
                                "avgNetApy7d": 0.035,
                                "avgNetApy30d": 0.033,
                            },
                            "candidates": [
                                {
                                    "vault": {"address": "0xB2", "name": "Better USDC", "version": "v2"},
                                    "avgNetApy7d": 0.05,
                                    "avgNetApy30d": 0.048,
                                    "delta7d": 0.015,
                                    "estimatedYearlyGainUsd": 15,
                                    "rewardShare7d": 0.1,
                                    "positionShareOfTvl": 0.0001,
                                    "young": False,
                                    "warnings": [],
                                }
                            ],
                            "migrationCostEstimate": None,
                        }
                    ],
                }
            ],
        }

    def get_capabilities(self) -> WalletCapabilities:
        return WalletCapabilities(
            backend=self.name, chain=self.chain, custody_model="local_service_vault", sign_only=False,
            has_signer=True, can_get_address=True, can_get_balance=True, can_sign_message=False,
            can_sign_transaction=True, can_send_transaction=True, external_dependencies=["wdk-evm-wallet"],
        )


def _backdate_state(days: float = 0, failed_hours_ago: float | None = None) -> None:
    path = morpho_monitor.state_path()
    state = json.loads(path.read_text(encoding="utf-8"))
    wallet = state["wallets"][OWNER.lower()]
    now = datetime.now(timezone.utc)
    if days:
        wallet["last_scan_at"] = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    if failed_hours_ago is not None:
        wallet["auto_scan_failed_at"] = (now - timedelta(hours=failed_hours_ago)).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(state), encoding="utf-8")


async def _main() -> None:
    backend = FakeScanBackend()
    adapter = OpenClawWalletAdapter(backend)

    first = await adapter.invoke("get_wallet_address", {})
    assert first.ok is True
    assert first.data.get("address") == OWNER, "the original answer is kept"
    notice = first.data["morpho_monitor_notice"]
    assert notice["new_opportunity_count"] == 1
    [row] = notice["new_opportunities"]
    assert row["from_vault"]["name"] == "Current USDC" and row["to_vault"]["name"] == "Better USDC"
    assert row["delta_7d"] == 0.015 and row["asset"] == "USDC"
    assert "Do not move funds" in notice["agent_instruction"]
    assert backend.scan_calls == [{"include_migration_cost": False}], "auto scan skips the slow cost estimate"

    second = await adapter.invoke("get_wallet_address", {})
    assert "morpho_monitor_notice" not in second.data
    assert len(backend.scan_calls) == 1, "no second scan before the interval"

    # Three days later the same candidate is scanned again but is not news any more.
    _backdate_state(days=3.1)
    later = await adapter.invoke("get_wallet_address", {})
    assert len(backend.scan_calls) == 2
    assert "morpho_monitor_notice" not in later.data

    # A failing scan never breaks the answer and waits before retrying.
    _backdate_state(days=3.1)
    backend.fail = True
    failed = await adapter.invoke("get_wallet_address", {})
    assert failed.ok is True and failed.data.get("address") == OWNER
    assert "morpho_monitor_notice" not in failed.data
    assert len(backend.scan_calls) == 3
    await adapter.invoke("get_wallet_address", {})
    assert len(backend.scan_calls) == 3, "no retry right after a failure"
    _backdate_state(failed_hours_ago=7)
    backend.fail = False
    await adapter.invoke("get_wallet_address", {})
    assert len(backend.scan_calls) == 4, "retried after the backoff"

    # A slow scan is cut off by the timeout; the answer still comes back.
    _backdate_state(days=3.1)
    backend.delay = 0.5
    original_timeout = morpho_monitor.AUTO_SCAN_TIMEOUT_SECONDS
    morpho_monitor.AUTO_SCAN_TIMEOUT_SECONDS = 0.05
    try:
        slow = await adapter.invoke("get_wallet_address", {})
    finally:
        morpho_monitor.AUTO_SCAN_TIMEOUT_SECONDS = original_timeout
        backend.delay = 0.0
    assert slow.ok is True and "morpho_monitor_notice" not in slow.data

    # Opt-out.
    _backdate_state(days=3.1, failed_hours_ago=7)
    calls = len(backend.scan_calls)
    os.environ[morpho_monitor.AUTO_SCAN_ENV] = "0"
    try:
        await adapter.invoke("get_wallet_address", {})
    finally:
        os.environ.pop(morpho_monitor.AUTO_SCAN_ENV, None)
    assert len(backend.scan_calls) == calls, "AGENT_WALLET_MORPHO_AUTO_SCAN=0 disables the scan"

    # Write tools and the monitor's own tools never trigger it.
    for tool, args in (
        ("transfer_evm_native", {"to": OWNER, "amount_wei": "1", "mode": "preview", "purpose": "t"}),
        ("get_morpho_monitor_schedule", {"host": "codex"}),
    ):
        await adapter.invoke(tool, args)
    assert len(backend.scan_calls) == calls

    # A non-EVM backend is left alone.
    class FakeSolanaBackend(FakeScanBackend):
        name = "solana_local"
        chain = "solana"

    solana = FakeSolanaBackend()
    await OpenClawWalletAdapter(solana).invoke("get_wallet_address", {})
    assert solana.scan_calls == []


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_home:
        install_test_sealed_secrets(
            Path(temp_home),
            boot_key="test-boot-key-for-morpho-auto-scan",
            master_key="test-master-key-for-morpho-auto-scan",
            approval_secret="test-approval-secret-for-morpho-auto-scan",
        )
        asyncio.run(_main())
    print("smoke_morpho_auto_scan: ok")


if __name__ == "__main__":
    main()
