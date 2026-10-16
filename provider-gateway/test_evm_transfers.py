"""Contract smoke checks for the bounded Alchemy transfer route."""

import asyncio
from unittest.mock import patch

import httpx

from app import app


ADDRESS = "0x" + "1" * 40


async def main() -> None:
    calls = []

    async def fake_post(url, *, json_body):
        calls.append((url, json_body))
        return 200, {"result": {"transfers": [], "pageKey": "next"}}

    transport = httpx.ASGITransport(app=app)
    with patch("app._http_post", fake_post), patch(
        "app._resolve_evm_rpc_url", return_value=("alchemy", "https://alchemy.example/v2/key")
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/v1/evm/transfers/base?address={ADDRESS}&direction=outgoing")
            assert response.status_code == 200, response.text
            assert response.json()["page_key"] == "next"
            params = calls[0][1]["params"][0]
            assert params["fromAddress"] == ADDRESS
            assert params["maxCount"] == "0x32"
            assert (await client.get("/v1/evm/transfers/base?address=bad")).status_code == 400
            assert (await client.get(f"/v1/evm/transfers/base?address={ADDRESS}&page_size=1000")).status_code == 400


if __name__ == "__main__":
    asyncio.run(main())
