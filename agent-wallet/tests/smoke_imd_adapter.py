"""Smoke coverage for the IMD tools on the OpenClaw adapter surface."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _secret_test_utils import install_test_sealed_secrets  # noqa: E402

os.environ["AGENT_WALLET_IMD_STATE_DIR"] = tempfile.mkdtemp(prefix="imd-adapter-state-")

from agent_wallet import autonomous_permissions  # noqa: E402
from agent_wallet.approval import issue_approval_token  # noqa: E402
from agent_wallet.openclaw_adapter import OpenClawWalletAdapter  # noqa: E402
from agent_wallet.providers import imd  # noqa: E402
from agent_wallet.wallet_layer.base import AgentWalletBackend, WalletCapabilities  # noqa: E402
from smoke_imd_provider import HALF, OWNER, FakeEvmBackend, FakeImdApi  # noqa: E402


class FakeAdapterBackend(FakeEvmBackend, AgentWalletBackend):
    name = "wdk_evm_local"
    chain = "evm"

    def __init__(self, network: str = "base"):
        super().__init__()
        self.network = network
        self.approve_calls: list[str] = []

    def with_network(self, network: str):
        clone = FakeAdapterBackend(network)
        clone.permits, clone.approvals, clone.approve_calls = self.permits, self.approvals, self.approve_calls
        return clone

    async def get_balance(self, address=None):
        return {}

    def get_capabilities(self) -> WalletCapabilities:
        return WalletCapabilities(
            backend=self.name, chain="evm", custody_model="local", sign_only=False, has_signer=True
        )

    async def quote_imd_permit2_approval(self, amount_raw):
        return {
            "network": "ethereum",
            "address": OWNER,
            "token": imd.IMD_TOKEN_ADDRESS,
            "spender": imd.PERMIT2_ADDRESS,
            "amountRaw": str(amount_raw),
            "currentAllowanceRaw": "0",
            "ethBalanceWei": "1000",
            "estimatedFeeWei": "42",
        }

    async def approve_imd_for_permit2(self, amount_raw):
        self.approve_calls.append(str(amount_raw))
        return {"network": "ethereum", "tx_hash": "0xapprove", "confirmed": True, "confirmation_status": "confirmed"}


def _token_for(tool_name: str, preview: dict) -> str:
    return issue_approval_token(
        tool_name=tool_name,
        network="base",
        summary=preview["confirmation_summary"],
        mainnet_confirmed=True,
    )


async def main() -> None:
    install_test_sealed_secrets(
        Path(tempfile.mkdtemp(prefix="openclaw-imd-adapter-")),
        boot_key="test-boot-key-for-imd-adapter-smoke",
        approval_secret="imd-adapter-smoke-secret",
    )
    autonomous_permissions.revoke_all()
    api = FakeImdApi()
    imd.get_client = lambda: api  # type: ignore[assignment]
    imd.ORDER_POLL_INTERVAL_SECONDS = 0

    backend = FakeAdapterBackend("base")
    adapter = OpenClawWalletAdapter(backend)
    names = {tool.name for tool in adapter.list_tools()}
    assert {"imd_status", "imd_check", "imd_open_request", "imd_prepare_allowance", "imd_get_job"} <= names

    status = await adapter.invoke("imd_status", {})
    assert status.ok, status.error
    assert status.data["ready"] is True and status.data["price_per_action"] == "0.5"

    body = {"objective": "Summarize three EVM testing approaches.", "skill": "research-report", "github": False}

    # Execute without any approval is refused before anything is signed.
    refused = await adapter.invoke(
        "imd_open_request",
        {"action": "job.open", "input": body, "purpose": "report", "mode": "intent_execute"},
    )
    assert not refused.ok and "approval_token" in refused.error, refused.error
    assert backend.permits == []

    preview = await adapter.invoke(
        "imd_open_request",
        {"action": "job.open", "input": body, "purpose": "report", "mode": "intent_preview"},
    )
    assert preview.ok, preview.error
    summary = preview.data["confirmation_summary"]
    assert summary["amount_raw"] == HALF and summary["imd_action"] == "job.open"
    assert summary["recipient"].lower() == "0x4e0fa57bde726079356537e2f34d671e9f41adbc"
    assert preview.data["mode"] == "preview" and preview.data["is_mainnet"] is True
    token = _token_for("imd_open_request", preview.data)

    # A token for one body cannot pay for another.
    tampered = await adapter.invoke(
        "imd_open_request",
        {
            "action": "job.open",
            "input": {**body, "objective": "Something else entirely."},
            "purpose": "report",
            "mode": "intent_execute",
            "approval_token": token,
        },
    )
    assert not tampered.ok and "input_sha256" in tampered.error, tampered.error
    assert backend.permits == []

    paid = await adapter.invoke(
        "imd_open_request",
        {"action": "job.open", "input": body, "purpose": "report", "mode": "intent_execute", "approval_token": token},
    )
    assert paid.ok, paid.error
    assert paid.data["status"] == "admitted" and paid.data["order_id"] == "ORDER_1"
    assert len(backend.permits) == 1 and len(backend.approvals) == 1

    # Approval tokens are single-use.
    replay = await adapter.invoke(
        "imd_open_request",
        {"action": "job.open", "input": body, "purpose": "report", "mode": "intent_execute", "approval_token": token},
    )
    assert not replay.ok, "replayed approval token must be refused"

    allowance_preview = await adapter.invoke(
        "imd_prepare_allowance", {"amount_raw": HALF, "mode": "intent_preview"}
    )
    assert allowance_preview.ok, allowance_preview.error
    assert allowance_preview.data["confirmation_summary"]["spender"] == imd.PERMIT2_ADDRESS
    too_much = await adapter.invoke(
        "imd_prepare_allowance", {"amount_raw": str(6 * 10**18), "mode": "intent_preview"}
    )
    assert not too_much.ok and "5 IMD" in too_much.error
    allowance_token = _token_for("imd_prepare_allowance", allowance_preview.data)
    wrong_amount = await adapter.invoke(
        "imd_prepare_allowance",
        {"amount_raw": "1000000000000000000", "mode": "intent_execute", "approval_token": allowance_token},
    )
    assert not wrong_amount.ok and "amount_raw" in wrong_amount.error, wrong_amount.error
    approved = await adapter.invoke(
        "imd_prepare_allowance",
        {"amount_raw": HALF, "mode": "intent_execute", "approval_token": allowance_token},
    )
    assert approved.ok, approved.error
    assert backend.approve_calls == [HALF]

    bad_job = await adapter.invoke("imd_get_job", {"job_id": "../requests"})
    assert not bad_job.ok
    print("smoke_imd_adapter: ok")


if __name__ == "__main__":
    asyncio.run(main())
