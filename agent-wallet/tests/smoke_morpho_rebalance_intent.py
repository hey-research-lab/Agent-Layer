"""Smoke tests for rebalance_evm_morpho_vault_position (withdraw -> supply, one approval)."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _secret_test_utils import install_test_sealed_secrets  # noqa: E402
from agent_wallet.approval import issue_approval_token  # noqa: E402
from agent_wallet.openclaw_adapter import OpenClawWalletAdapter  # noqa: E402
from agent_wallet.wallet_layer.base import AgentWalletBackend, WalletBackendError, WalletCapabilities  # noqa: E402

TOOL = "rebalance_evm_morpho_vault_position"
OWNER = "0x1111111111111111111111111111111111111111"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
SOURCE = "0x00000000000000000000000000000000000000a1"
TARGET = "0x00000000000000000000000000000000000000b2"
OTHER_ASSET_VAULT = "0x00000000000000000000000000000000000000c3"


class FakeMorphoBackend(AgentWalletBackend):
    name = "wdk_evm_local"
    chain = "evm"
    network = "base"
    sign_only = False

    def __init__(self) -> None:
        self.position_assets = "250000000"
        self.sent: list[dict[str, Any]] = []
        self.fail_supply = False

    def with_network(self, network: str) -> "FakeMorphoBackend":
        self.network = str(network).strip().lower()
        return self

    async def get_address(self) -> str | None:
        return OWNER

    async def get_balance(self, address: str | None = None) -> dict:
        return {"chain": "evm", "network": self.network, "address": OWNER, "balance_wei": "0"}

    async def get_evm_morpho_vaults(self, *, vault_address: str | None = None, **_: Any) -> dict:
        vaults = {
            SOURCE: {"address": SOURCE, "name": "Source", "version": "v1", "listed": True,
                     "asset": {"address": USDC, "symbol": "USDC", "decimals": 6},
                     "netApy": 0.035, "avgNetApy7d": 0.0349, "avgNetApy30d": 0.0333, "warnings": []},
            TARGET: {"address": TARGET, "name": "Target", "version": "v2", "listed": True,
                     "asset": {"address": USDC, "symbol": "USDC", "decimals": 6},
                     "netApy": 0.047, "avgNetApy7d": 0.0467, "avgNetApy30d": 0.0449,
                     "warnings": [{"type": "x", "level": "YELLOW"}]},
            OTHER_ASSET_VAULT: {"address": OTHER_ASSET_VAULT, "name": "WETH", "version": "v2",
                                "asset": {"address": "0x4200000000000000000000000000000000000006"}},
        }
        vault = vaults.get(str(vault_address))
        return {"found": vault is not None, "vault": vault}

    async def get_evm_morpho_positions(self) -> dict:
        return {
            "address": OWNER,
            "vault_position_count": 1,
            "vault_positions": [
                {"version": "v1", "vault": {"address": SOURCE, "asset": {"address": USDC}},
                 "assets": self.position_assets, "assetsUsd": 250},
            ],
        }

    async def preview_evm_morpho_vault_operation(self, *, operation: str, token_address: str,
                                                 vault_address: str | None = None, amount_raw: str | None = None,
                                                 **_: Any) -> dict:
        return {"operation": operation, "estimated_fee_wei": "1000", "amount_raw": amount_raw,
                "requirements": {"required": operation == "supply"}}

    async def send_evm_morpho_vault_operation(self, *, operation: str, token_address: str,
                                              vault_address: str | None = None, amount_raw: str | None = None,
                                              **_: Any) -> dict:
        if operation == "supply" and self.fail_supply:
            raise WalletBackendError("execution reverted: supply cap reached")
        self.sent.append({"operation": operation, "vault": vault_address, "amount_raw": amount_raw,
                          "token": token_address})
        return {"operation": operation, "confirmed": True, "tx_hash": "0x" + str(len(self.sent)) * 64}

    def get_capabilities(self) -> WalletCapabilities:
        return WalletCapabilities(
            backend=self.name, chain=self.chain, custody_model="local_service_vault", sign_only=False,
            has_signer=True, can_get_address=True, can_get_balance=True, can_sign_message=False,
            can_sign_transaction=True, can_send_transaction=True, external_dependencies=["wdk-evm-wallet"],
        )


def _args(mode: str, **extra: Any) -> dict[str, Any]:
    return {"from_vault_address": SOURCE, "to_vault_address": TARGET, "mode": mode,
            "purpose": "move to a better vault", "network": "base", **extra}


def _token(summary: dict[str, Any]) -> str:
    return issue_approval_token(tool_name=TOOL, network="base", summary=summary,
                                mainnet_confirmed=True, issued_by="test")


async def _main() -> None:
    backend = FakeMorphoBackend()
    adapter = OpenClawWalletAdapter(backend)
    assert TOOL in {tool.name for tool in adapter.list_tools()}

    preview = await adapter.invoke(TOOL, _args("intent_preview"))
    assert preview.ok is True, preview.error
    data = preview.data
    summary = data["confirmation_summary"]
    assert summary["from_vault"] == SOURCE and summary["to_vault"] == TARGET
    assert summary["amount_raw"] == "250000000", "default amount is the whole position"
    assert summary["owner"] == OWNER and summary["network"] == "base"
    assert data["moves_full_position"] is True
    assert data["amount_ui"] == "250"
    assert data["to_vault"]["avg_net_apy_7d"] == 0.0467
    assert data["is_mainnet"] is True
    assert backend.sent == [], "preview must not send anything"

    # Execute without any approval is refused before anything is sent.
    refused = await adapter.invoke(TOOL, _args("intent_execute"))
    assert refused.ok is False and backend.sent == []

    # A token for a different target vault does not authorize this move.
    tampered = await adapter.invoke(
        TOOL, _args("intent_execute", to_vault_address=TARGET,
                    approval_token=_token({**summary, "to_vault": OTHER_ASSET_VAULT}))
    )
    assert tampered.ok is False and "to_vault" in (tampered.error or ""), tampered.error
    assert backend.sent == []

    # An approval for SOURCE -> TARGET cannot be used for SOURCE -> another vault.
    redirected = await adapter.invoke(
        TOOL, _args("intent_execute", to_vault_address=OTHER_ASSET_VAULT, approval_token=_token(summary))
    )
    assert redirected.ok is False and "to_vault" in (redirected.error or ""), redirected.error
    assert backend.sent == []

    token = _token(summary)
    executed = await adapter.invoke(TOOL, _args("intent_execute", approval_token=token))
    assert executed.ok is True, executed.error
    assert executed.data["status"] == "completed"
    assert executed.data["funds_location"] == "target_vault"
    assert [(tx["operation"], tx["vault"], tx["amount_raw"]) for tx in backend.sent] == [
        ("withdraw", SOURCE, "250000000"),
        ("supply", TARGET, "250000000"),
    ]

    # The same token cannot be replayed.
    replay = await adapter.invoke(TOOL, _args("intent_execute", approval_token=token))
    assert replay.ok is False and len(backend.sent) == 2

    # Supply failure: funds stay in the wallet, reported as partial, no retry.
    backend.sent.clear()
    backend.fail_supply = True
    partial_preview = await adapter.invoke(TOOL, _args("intent_preview", amount_raw="100000000"))
    partial = await adapter.invoke(
        TOOL, _args("intent_execute", amount_raw="100000000",
                    approval_token=_token(partial_preview.data["confirmation_summary"]))
    )
    assert partial.ok is False
    assert partial.data["status"] == "partial" and partial.data["funds_location"] == "wallet"
    assert [tx["operation"] for tx in backend.sent] == ["withdraw"]
    backend.fail_supply = False

    # The position shrank after approval: nothing is sent.
    backend.sent.clear()
    shrink_preview = await adapter.invoke(TOOL, _args("intent_preview"))
    backend.position_assets = "1000"
    shrunk = await adapter.invoke(
        TOOL, _args("intent_execute", approval_token=_token(shrink_preview.data["confirmation_summary"]))
    )
    assert shrunk.ok is False and "no longer covers" in (shrunk.error or ""), shrunk.error
    assert backend.sent == []
    backend.position_assets = "250000000"

    # Guard rails at preview time.
    over = await adapter.invoke(TOOL, _args("intent_preview", amount_raw="999999999999"))
    assert over.ok is False and "exceeds the position" in (over.error or "")
    cross_asset = await adapter.invoke(TOOL, _args("intent_preview", to_vault_address=OTHER_ASSET_VAULT))
    assert cross_asset.ok is False and "same asset" in (cross_asset.error or "")
    same = await adapter.invoke(TOOL, _args("intent_preview", to_vault_address=SOURCE))
    assert same.ok is False


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_home:
        install_test_sealed_secrets(
            Path(temp_home),
            boot_key="test-boot-key-for-morpho-rebalance",
            master_key="test-master-key-for-morpho-rebalance",
            approval_secret="test-approval-secret-for-morpho-rebalance",
        )
        asyncio.run(_main())
    print("smoke_morpho_rebalance_intent: ok")


if __name__ == "__main__":
    main()
