"""On-demand EVM transfer activity with a small process-local page cache."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from agent_wallet.config import settings, resolve_openclaw_home
from agent_wallet.exceptions import ProviderError
from agent_wallet.http_client import get_client

_CACHE: dict[tuple[str, str, str, str, int], tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 30.0


def _pending_path(network: str, address: str) -> Path:
    return resolve_openclaw_home() / "cache" / "evm-activity" / f"{network}-{address.lower()}.json"


def _read_pending(network: str, address: str) -> list[dict[str, Any]]:
    path = _pending_path(network, address)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    cutoff = time.time() - 86400
    return [item for item in data if isinstance(item, dict) and item.get("recorded_at", 0) >= cutoff][:100]


def record_submitted_transaction(network: str, address: str, tx_hash: str) -> None:
    """Keep a local placeholder until Alchemy indexes a wallet-submitted tx."""
    if network not in {"ethereum", "base", "robinhood"}:
        return
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
        return
    path = _pending_path(network, address)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    entries = [item for item in _read_pending(network, address) if item.get("hash", "").lower() != tx_hash.lower()]
    entries.insert(0, {"hash": tx_hash, "recorded_at": time.time()})
    fd, temp_name = tempfile.mkstemp(prefix=".activity-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entries[:100], stream, separators=(",", ":"))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


async def _fetch_page(
    network: str, address: str, direction: str, page_key: str, page_size: int
) -> dict[str, Any]:
    if page_key == "__end__":
        return {"transfers": [], "page_key": None}
    cache_key = (network, address.lower(), direction, page_key, page_size)
    cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1]
    gateway_url = str(settings.provider_gateway_url or "").strip()
    if not gateway_url:
        raise ProviderError("evm-activity", "Provider gateway URL is required for EVM activity.")
    bearer = str(settings.provider_gateway_bearer_token or "").strip()
    params: dict[str, Any] = {
        "address": address,
        "direction": direction,
        "page_size": page_size,
    }
    if page_key:
        params["page_key"] = page_key
    try:
        response = await get_client().get(
            f"{gateway_url.rstrip('/')}/v1/evm/transfers/{network}",
            params=params,
            headers={"Authorization": f"Bearer {bearer}"} if bearer else None,
        )
    except Exception as exc:
        raise ProviderError("evm-activity", f"Gateway request failed: {exc}") from exc
    if response.status_code != 200:
        raise ProviderError("evm-activity", f"Gateway returned HTTP {response.status_code}.")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("transfers"), list):
        raise ProviderError("evm-activity", "Gateway returned invalid transfer data.")
    if len(_CACHE) >= 128:
        _CACHE.clear()
    _CACHE[cache_key] = (time.monotonic(), data)
    return data


async def get_activity(
    network: str, address: str, *, page_size: int = 50,
    outgoing_page_key: str = "", incoming_page_key: str = "",
) -> dict[str, Any]:
    if network not in {"ethereum", "base", "robinhood"}:
        raise ValueError("Activity is supported on ethereum, base, and robinhood.")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        raise ValueError("Wallet address must be a 20-byte EVM address.")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100.")
    for key in (outgoing_page_key, incoming_page_key):
        if not isinstance(key, str) or len(key) > 2048:
            raise ValueError("Invalid activity page key.")
    outgoing, incoming = await asyncio.gather(
        _fetch_page(network, address, "outgoing", outgoing_page_key, page_size),
        _fetch_page(network, address, "incoming", incoming_page_key, page_size),
    )
    by_hash: dict[str, dict[str, Any]] = {}
    for direction, page in (("outgoing", outgoing), ("incoming", incoming)):
        for transfer in page["transfers"]:
            if not isinstance(transfer, dict):
                continue
            tx_hash = str(transfer.get("hash") or "")
            if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
                continue
            entry = by_hash.setdefault(tx_hash.lower(), {
                "hash": tx_hash,
                "network": network,
                "block_number": transfer.get("blockNum"),
                "timestamp": (transfer.get("metadata") or {}).get("blockTimestamp"),
                "transfers": [],
            })
            unique_id = transfer.get("uniqueId")
            if not any(item.get("uniqueId") == unique_id for item in entry["transfers"]):
                entry["transfers"].append({**transfer, "direction": direction})
    transactions = sorted(
        by_hash.values(), key=lambda item: str(item.get("timestamp") or ""), reverse=True
    )
    for pending in reversed(_read_pending(network, address)):
        tx_hash = pending.get("hash", "")
        if tx_hash.lower() not in by_hash:
            transactions.insert(0, {
                "hash": tx_hash, "network": network, "status": "submitted",
                "timestamp": None, "transfers": [],
            })
    return {
        "network": network,
        "address": address,
        "transactions": transactions,
        "next_outgoing_page_key": outgoing.get("page_key") or "__end__",
        "next_incoming_page_key": incoming.get("page_key") or "__end__",
        "source": "alchemy-transfers",
        "coverage": "asset movements; failed and transfer-free contract calls are not indexed",
    }
