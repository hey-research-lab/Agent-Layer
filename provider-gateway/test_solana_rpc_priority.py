"""Verify automatic Solana RPC selection prefers Alchemy."""

import os
from unittest.mock import patch

from app import _resolve_rpc_url, _status_payload


def main() -> None:
    names = (
        "SHARED_SOLANA_RPC_URL", "ALCHEMY_SHARED_RPC_URL", "ALCHEMY_API_KEY",
        "HELIUS_SHARED_RPC_URL", "HELIUS_API_KEY",
    )
    with patch.dict(os.environ, {name: "" for name in names}):
        os.environ["ALCHEMY_API_KEY"] = "alchemy-test"
        os.environ["HELIUS_API_KEY"] = "helius-test"
        assert _resolve_rpc_url("auto", "mainnet")[0] == "alchemy"
        assert _resolve_rpc_url("helius", "mainnet")[0] == "helius"
        assert _status_payload()["rpc_upstreams"] == ["alchemy", "helius"]
        os.environ["SHARED_SOLANA_RPC_URL"] = "https://custom.example/rpc"
        assert _resolve_rpc_url("auto", "mainnet") == ("shared", "https://custom.example/rpc")
        del os.environ["SHARED_SOLANA_RPC_URL"]
        del os.environ["ALCHEMY_API_KEY"]
        assert _resolve_rpc_url("auto", "mainnet")[0] == "helius"


if __name__ == "__main__":
    main()
