"""Smoke tests for the Morpho read builders in wallet_layer/wdk_evm.py.

The positions builder reshapes the daemon's JSON through an explicit field
allow-list, so a summary field the daemon adds is invisible to the agent unless
the builder names it. Vault and position records pass through unchanged, which
is how the V1/V2 `version` field and the named APY windows reach the agent.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_wallet.wallet_layer.wdk_evm import WdkEvmLocalWalletBackend  # noqa: E402

ADDRESS = "0x1111111111111111111111111111111111111111"


class _StubClient:
    """Stands in for WdkEvmLocalClient, returning one canned daemon payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, body))
        return self.payload


def _backend(payload: dict[str, Any]) -> WdkEvmLocalWalletBackend:
    backend = WdkEvmLocalWalletBackend(
        service_url="http://127.0.0.1:1",
        wallet_id="test-wallet",
        network="base",
        address=ADDRESS,
    )
    backend.client = _StubClient(payload)
    return backend


def _check_positions() -> None:
    position = {
        "version": "v1",
        "vault": {"address": "0xvault", "netApy": 0.035, "avgNetApy7d": 0.0349, "avgNetApy30d": 0.0333},
        "shares": "9",
        "assets": "250000000",
        "assetsUsd": 250,
    }
    backend = _backend(
        {
            "network": "base",
            "chainId": 8453,
            "address": ADDRESS,
            "protocol": "morpho",
            "marketPositionCount": 0,
            "vaultPositionCount": 1,
            "hiddenDustVaultPositionCount": 4,
            "dustThresholdUsd": 1,
            "marketPositions": [],
            "vaultPositions": [position],
        }
    )
    result = asyncio.run(backend.get_evm_morpho_positions())
    assert result["vault_position_count"] == 1
    assert result["hidden_dust_vault_position_count"] == 4, "dust count must reach the agent"
    assert result["dust_threshold_usd"] == 1
    assert result["vault_positions"] == [position], "position records must pass through unchanged"


def _check_vaults() -> None:
    vault = {"version": "v2", "address": "0xvault", "netApy": 0.05, "avgNetApy7d": 0.049, "avgNetApy30d": 0.047}
    backend = _backend({"network": "base", "chainId": 8453, "vaultCount": 1, "vaults": [vault]})
    result = asyncio.run(backend.get_evm_morpho_vaults())
    assert result["vault_count"] == 1
    assert result["vaults"] == [vault], "vault records must pass through unchanged"


def main() -> None:
    _check_positions()
    _check_vaults()
    print("smoke_wdk_evm_morpho_read_fields: ok")


if __name__ == "__main__":
    main()
