"""Focused checks for EVM activity grouping and pagination."""

import asyncio
from unittest.mock import patch

from agent_wallet.providers.evm_activity import get_activity


ADDRESS = "0x" + "1" * 40
HASH = "0x" + "a" * 64


async def main() -> None:
    calls = []

    async def fake_page(network, address, direction, page_key, page_size):
        calls.append((direction, page_key))
        if page_key == "__end__":
            return {"transfers": [], "page_key": None}
        return {
            "transfers": [{
                "hash": HASH,
                "uniqueId": f"{HASH}:{direction}",
                "blockNum": "0x10",
                "metadata": {"blockTimestamp": "2026-10-16T00:00:00Z"},
            }],
            "page_key": "older" if direction == "outgoing" else None,
        }

    with patch("agent_wallet.providers.evm_activity._fetch_page", fake_page):
        result = await get_activity("base", ADDRESS)
        assert len(result["transactions"]) == 1
        assert len(result["transactions"][0]["transfers"]) == 2
        assert result["next_outgoing_page_key"] == "older"
        assert result["next_incoming_page_key"] == "__end__"
        await get_activity("base", ADDRESS, outgoing_page_key="older", incoming_page_key="__end__")
        assert calls[-1] == ("incoming", "__end__")


if __name__ == "__main__":
    asyncio.run(main())
