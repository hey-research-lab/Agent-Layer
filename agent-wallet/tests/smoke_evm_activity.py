"""Focused checks for EVM activity grouping and pagination."""

import asyncio
import tempfile
import time
from unittest.mock import patch

from agent_wallet.providers.evm_activity import _write_pending, get_activity, record_submitted_transaction


ADDRESS = "0x" + "1" * 40
HASH = "0x" + "a" * 64
OLD_HASH = "0x" + "c" * 64


async def main() -> None:
    calls = []

    async def fake_page(network, address, direction, page_key, page_size, include_nfts):
        calls.append((direction, page_key, page_size, include_nfts))
        if page_key == "__end__":
            return {"transfers": [], "page_key": None}
        transfers = [{
            "id": f"{HASH}:{direction}",
            "hash": HASH,
            "block_number": 16,
            "timestamp": "2026-10-16T00:00:00Z",
        }]
        if direction == "incoming":
            transfers.append({
                "id": f"{OLD_HASH}:incoming",
                "hash": OLD_HASH,
                "block_number": 8,
                "timestamp": "2026-01-01T00:00:00Z",
            })
        return {"transfers": transfers, "page_key": "older" if direction == "outgoing" else None}

    with patch("agent_wallet.providers.evm_activity._fetch_page", fake_page):
        result = await get_activity("base", ADDRESS)
        assert len(result["transactions"]) == 2
        assert len(result["transactions"][0]["transfers"]) == 2
        assert result["next_outgoing_page_key"] == "older"
        assert result["next_incoming_page_key"] == "__end__"
        # Outgoing has more pages, so only activity since its oldest transfer is complete.
        assert result["complete_since"] == "2026-10-16T00:00:00Z"
        assert calls[0][2:] == (20, False)

        calls.clear()
        sent = await get_activity("base", ADDRESS, direction="outgoing", include_nfts=True)
        assert [call[0] for call in calls] == ["outgoing"]
        assert calls[0][3] is True
        assert "next_incoming_page_key" not in sent

        await get_activity("base", ADDRESS, outgoing_page_key="older", incoming_page_key="__end__")
        assert calls[-1][:2] == ("incoming", "__end__")
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"OPENCLAW_HOME": directory}):
            pending_hash = "0x" + "b" * 64
            record_submitted_transaction("base", ADDRESS, pending_hash)
            pending_result = await get_activity("base", ADDRESS)
            assert pending_result["transactions"][0]["hash"] == pending_hash
            assert pending_result["transactions"][0]["status"] == "submitted"
            older_result = await get_activity("base", ADDRESS, outgoing_page_key="older")
            assert all(item["hash"] != pending_hash for item in older_result["transactions"])

            # Stale placeholders settle from one receipt read each, and verdicts persist.
            now = time.time()
            hashes = {name: "0x" + digit * 64 for name, digit in
                      (("reverted", "d"), ("approve", "e"), ("dropped", "f"), ("pending", "1"), ("fresh", "2"))}
            _write_pending("base", ADDRESS, [
                {"hash": hashes["reverted"], "recorded_at": now - 60},
                {"hash": hashes["approve"], "recorded_at": now - 60},
                {"hash": hashes["dropped"], "recorded_at": now - 3600},
                {"hash": hashes["pending"], "recorded_at": now - 60},
                {"hash": hashes["fresh"], "recorded_at": now},
            ])
            receipts = {
                hashes["reverted"]: {"status": "reverted", "receipt": {"blockNumber": "0x20"}},
                hashes["approve"]: {"status": "confirmed", "receipt": {"blockNumber": "0x21"}},
                hashes["dropped"]: {"status": "not_found", "receipt": None},
                hashes["pending"]: {"status": "pending", "receipt": None},
            }
            looked_up = []

            async def lookup(tx_hash):
                looked_up.append(tx_hash)
                return receipts[tx_hash]

            settled = await get_activity("base", ADDRESS, receipt_lookup=lookup)
            status = {item["hash"]: item.get("status") for item in settled["transactions"]}
            assert status[hashes["reverted"]] == "failed"
            assert status[hashes["approve"]] == "confirmed"
            assert status[hashes["dropped"]] == "dropped"
            assert status[hashes["pending"]] == "submitted"
            assert status[hashes["fresh"]] == "submitted"
            assert hashes["fresh"] not in looked_up
            assert next(item for item in settled["transactions"] if item["hash"] == hashes["reverted"])["block_number"] == 32

            # An incoming-only view never shows wallet-sent placeholders.
            incoming_only = await get_activity("base", ADDRESS, direction="incoming", receipt_lookup=lookup)
            assert all("status" not in item for item in incoming_only["transactions"])

            # Final verdicts are never re-read; an unresolved one waits for the recheck interval.
            looked_up.clear()
            again = await get_activity("base", ADDRESS, receipt_lookup=lookup)
            assert looked_up == []
            assert {item["hash"]: item.get("status") for item in again["transactions"]}[hashes["reverted"]] == "failed"


async def self_transfer_and_indexed_placeholder() -> None:
    self_hash = "0x" + "9" * 64
    indexed = {"value": True}

    async def fake_page(network, address, direction, page_key, page_size, include_nfts):
        transfers = []
        if indexed["value"]:
            # A send to self: Alchemy returns the same transfer id from both directions.
            transfers.append({"id": f"{self_hash}:external", "hash": self_hash, "timestamp": "2026-10-16T00:00:00Z"})
        return {"transfers": transfers, "page_key": None}

    with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"OPENCLAW_HOME": directory}), \
            patch("agent_wallet.providers.evm_activity._fetch_page", fake_page):
        record_submitted_transaction("base", ADDRESS, self_hash)
        result = await get_activity("base", ADDRESS)
        assert len(result["transactions"]) == 1
        assert [item["direction"] for item in result["transactions"][0]["transfers"]] == ["self"]
        # Once indexed, the placeholder is gone: when the entry scrolls off the
        # first page it must not resurface as submitted.
        indexed["value"] = False
        later = await get_activity("base", ADDRESS)
        assert later["transactions"] == []


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(self_transfer_and_indexed_placeholder())
