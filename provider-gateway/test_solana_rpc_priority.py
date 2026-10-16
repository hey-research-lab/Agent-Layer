"""Verify Solana gateway routing is Alchemy-only."""

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
        assert _resolve_rpc_url("alchemy", "mainnet")[0] == "alchemy"
        assert _status_payload()["rpc_upstreams"] == ["alchemy"]
        os.environ["SHARED_SOLANA_RPC_URL"] = "https://custom.example/rpc"
        assert _resolve_rpc_url("auto", "mainnet")[0] == "alchemy"
        for legacy_provider in ("helius", "shared"):
            try:
                _resolve_rpc_url(legacy_provider, "mainnet")
            except RuntimeError as exc:
                assert "Unsupported Solana RPC provider" in str(exc)
            else:
                raise AssertionError(f"Legacy provider {legacy_provider} was accepted")
        del os.environ["ALCHEMY_API_KEY"]
        try:
            _resolve_rpc_url("auto", "mainnet")
        except RuntimeError as exc:
            assert "Alchemy Solana RPC is not configured" in str(exc)
        else:
            raise AssertionError("Gateway routed without Alchemy")


if __name__ == "__main__":
    main()
