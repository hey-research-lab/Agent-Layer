"""On-demand EVM transfer activity; the provider gateway caches and budgets Alchemy reads."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent_wallet.config import settings, resolve_openclaw_home
from agent_wallet.exceptions import ProviderError
from agent_wallet.http_client import get_client

_DIRECTIONS = {"both": ("outgoing", "incoming"), "outgoing": ("outgoing",), "incoming": ("incoming",)}
# Already present on the grouped transaction; repeating them per transfer only adds tokens.
_TX_LEVEL_FIELDS = {"hash", "block_number", "timestamp"}
# A placeholder is resolved with one cheap receipt read and the verdict is stored,
# so each wallet transaction costs at most a few receipt reads over its lifetime.
_RECEIPT_MIN_AGE = 30.0
_RECEIPT_RECHECK = 30.0
_RECEIPT_MAX_PER_CALL = 5
_DROPPED_AFTER = 1800.0
_FINAL_STATUSES = {"confirmed", "failed", "dropped"}

ReceiptLookup = Callable[[str], Awaitable[dict[str, Any]]]


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


def _write_pending(network: str, address: str, entries: list[dict[str, Any]]) -> None:
    path = _pending_path(network, address)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".activity-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(entries[:100], stream, separators=(",", ":"))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def record_submitted_transaction(network: str, address: str, tx_hash: str) -> None:
    """Keep a local placeholder until Alchemy indexes a wallet-submitted tx."""
    if network not in {"ethereum", "base", "robinhood"}:
        return
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address) or not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
        return
    entries = [item for item in _read_pending(network, address) if item.get("hash", "").lower() != tx_hash.lower()]
    entries.insert(0, {"hash": tx_hash, "recorded_at": time.time()})
    _write_pending(network, address, entries)


async def _resolve_pending(
    network: str, address: str, entries: list[dict[str, Any]], receipt_lookup: ReceiptLookup
) -> None:
    """Settle stale placeholders from their receipts and persist final verdicts.

    Reverted and transfer-free transactions (e.g. ERC-20 approvals) never appear
    in the transfer feed, so without this they would read as "submitted" for a day.
    """
    now = time.time()
    due = [
        item for item in entries
        if item.get("status") not in _FINAL_STATUSES
        and now - item.get("recorded_at", 0) >= _RECEIPT_MIN_AGE
        and now - item.get("checked_at", 0) >= _RECEIPT_RECHECK
    ][:_RECEIPT_MAX_PER_CALL]
    if not due:
        return
    results = await asyncio.gather(*(receipt_lookup(item["hash"]) for item in due), return_exceptions=True)
    for item, result in zip(due, results):
        item["checked_at"] = now
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        if status == "reverted":
            item["status"] = "failed"
        elif status == "confirmed":
            item["status"] = "confirmed"
        elif status == "not_found" and now - item.get("recorded_at", 0) >= _DROPPED_AFTER:
            item["status"] = "dropped"
        else:
            continue
        block = ((result.get("receipt") or {}).get("blockNumber"))
        if isinstance(block, str) and block.startswith("0x"):
            item["block_number"] = int(block, 16)
    try:
        _write_pending(network, address, entries)
    except OSError:
        pass  # The verdict is still returned; it is just re-checked next time.


async def _fetch_page(
    network: str, address: str, direction: str, page_key: str, page_size: int, include_nfts: bool
) -> dict[str, Any]:
    if page_key == "__end__":
        return {"transfers": [], "page_key": None}
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
    if include_nfts:
        params["include_nfts"] = "1"
    try:
        response = await get_client().get(
            f"{gateway_url.rstrip('/')}/v1/evm/transfers/{network}",
            params=params,
            headers={"Authorization": f"Bearer {bearer}"} if bearer else None,
        )
    except Exception as exc:
        raise ProviderError("evm-activity", f"Gateway request failed: {exc}") from exc
    if response.status_code == 429:
        try:
            message = str(response.json().get("error") or "")
        except ValueError:
            message = ""
        raise ProviderError(
            "evm-activity",
            message or "Transfer history is temporarily rate limited.",
            details={"retry_after": response.headers.get("retry-after")},
        )
    if response.status_code != 200:
        raise ProviderError("evm-activity", f"Gateway returned HTTP {response.status_code}.")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("transfers"), list):
        raise ProviderError("evm-activity", "Gateway returned invalid transfer data.")
    return data


async def get_activity(
    network: str, address: str, *, page_size: int = 20, direction: str = "both",
    include_nfts: bool = False, outgoing_page_key: str = "", incoming_page_key: str = "",
    receipt_lookup: ReceiptLookup | None = None,
) -> dict[str, Any]:
    if network not in {"ethereum", "base", "robinhood"}:
        raise ValueError("Activity is supported on ethereum, base, and robinhood.")
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        raise ValueError("Wallet address must be a 20-byte EVM address.")
    if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100.")
    if direction not in _DIRECTIONS:
        raise ValueError("direction must be both, outgoing, or incoming.")
    for key in (outgoing_page_key, incoming_page_key):
        if not isinstance(key, str) or len(key) > 2048:
            raise ValueError("Invalid activity page key.")
    page_keys = {"outgoing": outgoing_page_key, "incoming": incoming_page_key}
    directions = _DIRECTIONS[direction]
    pages = dict(zip(directions, await asyncio.gather(*(
        _fetch_page(network, address, name, page_keys[name], page_size, bool(include_nfts))
        for name in directions
    ))))
    by_hash: dict[str, dict[str, Any]] = {}
    # A direction with more pages is only complete down to its oldest fetched
    # transfer; anything older from the other direction may still have gaps.
    complete_since: str | None = None
    for name, page in pages.items():
        timestamps = []
        for transfer in page["transfers"]:
            if not isinstance(transfer, dict):
                continue
            tx_hash = str(transfer.get("hash") or "")
            if not re.fullmatch(r"0x[a-fA-F0-9]{64}", tx_hash):
                continue
            if transfer.get("timestamp"):
                timestamps.append(str(transfer["timestamp"]))
            entry = by_hash.setdefault(tx_hash.lower(), {
                "hash": tx_hash,
                "network": network,
                "block_number": transfer.get("block_number"),
                "timestamp": transfer.get("timestamp"),
                "transfers": [],
            })
            transfer_id = transfer.get("id")
            existing = next(
                (item for item in entry["transfers"] if transfer_id is not None and item.get("id") == transfer_id),
                None,
            )
            if existing is not None:
                # The same transfer in both directions is a send to self.
                if existing["direction"] != name:
                    existing["direction"] = "self"
            else:
                entry["transfers"].append({
                    **{key: value for key, value in transfer.items() if key not in _TX_LEVEL_FIELDS},
                    "direction": name,
                })
        if page.get("page_key") and timestamps:
            oldest = min(timestamps)
            complete_since = max(complete_since or oldest, oldest)
    transactions = sorted(
        by_hash.values(), key=lambda item: str(item.get("timestamp") or ""), reverse=True
    )
    # Placeholders belong to the newest activity only, never to older pages.
    first_page = not outgoing_page_key and not incoming_page_key
    # Wallet-sent transactions are outgoing, so an incoming-only view never shows them.
    pending_entries = _read_pending(network, address) if first_page and direction != "incoming" else []
    unindexed = [item for item in pending_entries if item.get("hash", "").lower() not in by_hash]
    if len(unindexed) < len(pending_entries):
        # Once indexed, a placeholder is obsolete; keeping it would resurface it as
        # pending after the real entry scrolls past the first page.
        try:
            _write_pending(network, address, unindexed)
        except OSError:
            pass
        pending_entries = unindexed
    if unindexed and receipt_lookup is not None:
        await _resolve_pending(network, address, pending_entries, receipt_lookup)
    for pending in reversed(unindexed):
        placeholder = {
            "hash": pending.get("hash", ""), "network": network,
            "status": pending.get("status", "submitted"), "timestamp": None, "transfers": [],
        }
        if pending.get("block_number") is not None:
            placeholder["block_number"] = pending["block_number"]
        transactions.insert(0, placeholder)
    result: dict[str, Any] = {
        "network": network,
        "address": address,
        "direction": direction,
        "transactions": transactions,
        "complete_since": complete_since,
        "source": "alchemy-transfers",
        "coverage": (
            "asset movements; zero-value transfers, failed transactions and transfer-free "
            "contract calls are not indexed"
            + ("" if include_nfts else "; NFTs excluded unless include_nfts is true")
        ),
    }
    for name, page in pages.items():
        result[f"next_{name}_page_key"] = page.get("page_key") or "__end__"
    return result
