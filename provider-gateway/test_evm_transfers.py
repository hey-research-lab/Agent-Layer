"""Contract smoke checks for the bounded Alchemy transfer route."""

import asyncio
from unittest.mock import patch

import httpx

import app as gateway
from app import app


ADDRESS = "0x" + "1" * 40
HASH = "0x" + "a" * 64


async def main() -> None:
    calls = []

    async def fake_post(url, *, json_body):
        calls.append((url, json_body))
        return 200, {"result": {"transfers": [{
            "uniqueId": f"{HASH}:external",
            "hash": HASH,
            "blockNum": "0x10",
            "from": ADDRESS,
            "to": "0x" + "2" * 40,
            "value": 1.5,
            "asset": "ETH",
            "category": "external",
            "rawContract": {"value": "0x14d1120d7b160000", "address": None, "decimal": "0x12"},
            "metadata": {"blockTimestamp": "2026-10-16T00:00:00Z"},
        }], "pageKey": "next"}}

    nft = gateway._compact_transfer({
        "uniqueId": f"{HASH}:log:1", "hash": HASH, "category": "erc1155", "value": None,
        "rawContract": {"value": None, "address": "0x" + "4" * 40, "decimal": None},
        "erc1155Metadata": [{"tokenId": "0x01", "value": "0x05"}, {"tokenId": "0x02", "value": "0x01"}],
    })
    assert nft["erc1155"] == [{"token_id": "0x01", "value": "5"}, {"token_id": "0x02", "value": "1"}]
    assert "raw_value" not in nft and "value" not in nft

    transport = httpx.ASGITransport(app=app)
    with patch("app._http_post", fake_post), patch(
        "app._resolve_evm_rpc_url", return_value=("alchemy", "https://alchemy.example/v2/key")
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/v1/evm/transfers/base?address={ADDRESS}&direction=outgoing")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_key"] == "next"
            assert body["transfers"][0] == {
                "id": f"{HASH}:external",
                "hash": HASH,
                "block_number": 16,
                "timestamp": "2026-10-16T00:00:00Z",
                "category": "external",
                "from": ADDRESS,
                "to": "0x" + "2" * 40,
                "asset": "ETH",
                "value": 1.5,
                "raw_value": "1500000000000000000",
                "decimals": 18,
            }
            params = calls[0][1]["params"][0]
            assert params["fromAddress"] == ADDRESS
            assert params["maxCount"] == "0x14"
            assert params["excludeZeroValue"] is True
            assert params["category"] == ["external", "internal", "erc20"]

            # An identical request is served from cache without another Alchemy call.
            await client.get(f"/v1/evm/transfers/base?address={ADDRESS}&direction=outgoing")
            assert len(calls) == 1

            response = await client.get(f"/v1/evm/transfers/robinhood?address={ADDRESS}&direction=incoming&include_nfts=1")
            assert response.status_code == 200, response.text
            assert calls[-1][1]["params"][0]["category"] == ["external", "erc20", "erc721", "erc1155"]
            assert (await client.get("/v1/evm/transfers/base?address=bad")).status_code == 400
            assert (await client.get(f"/v1/evm/transfers/base?address={ADDRESS}&page_size=1000")).status_code == 400

            # The per-address cap stops a runaway loop on one wallet.
            with patch.object(gateway, "_TRANSFER_PER_ADDRESS_PER_MIN", 3):
                statuses = [
                    (await client.get(f"/v1/evm/transfers/ethereum?address={ADDRESS}&page_size={size}")).status_code
                    for size in (1, 2, 3, 4)
                ]
            assert statuses[-1] == 429, statuses

            # The daily cap bounds total Alchemy spend across all addresses.
            with patch.object(gateway, "_TRANSFER_MAX_PER_DAY", gateway._TRANSFER_DAY["count"]):
                other = "0x" + "3" * 40
                response = await client.get(f"/v1/evm/transfers/base?address={other}")
                assert response.status_code == 429
                assert int(response.headers["retry-after"]) > 0


if __name__ == "__main__":
    asyncio.run(main())
