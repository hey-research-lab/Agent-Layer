"""Verify the SDK-compatible Solana RPC route stays narrow and uses Alchemy."""

import asyncio
import os
from unittest.mock import patch

import httpx

import app as gateway


async def _run() -> None:
    forwarded = []

    async def fake_post(url, *, headers=None, json_body=None):
        forwarded.append((url, json_body))
        return 200, {"jsonrpc": "2.0", "id": json_body["id"], "result": {"value": 1}}

    original_post = gateway._http_post
    gateway._http_post = fake_post
    try:
        with patch.dict(os.environ, {
            "ALCHEMY_API_KEY": "test-key",
            "REQUIRE_BEARER_AUTH": "true",
            "PROVIDER_GATEWAY_BEARER_TOKEN": "test-token",
        }):
            transport = httpx.ASGITransport(app=gateway.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                payload = {"jsonrpc": "2.0", "id": 7, "method": "getAccountInfo", "params": ["addr"]}
                unauthorized = await client.post("/v1/rpc/x402", json=payload)
                assert unauthorized.status_code == 401
                denied = await client.post("/v1/rpc/x402?token=test-token", json={**payload, "method": "sendTransaction"})
                assert denied.status_code == 403
                allowed = await client.post("/v1/rpc/x402?token=test-token", json=payload)
                assert allowed.status_code == 200
                assert allowed.json() == {"jsonrpc": "2.0", "id": 7, "result": {"value": 1}}
                assert forwarded == [(
                    "https://solana-mainnet.g.alchemy.com/v2/test-key",
                    payload,
                )]
    finally:
        gateway._http_post = original_post


if __name__ == "__main__":
    asyncio.run(_run())
    print("test_x402_rpc: ok")
