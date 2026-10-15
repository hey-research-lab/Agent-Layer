"""Smoke tests for the Morpho rebalance monitor state (agent_wallet/morpho_monitor.py)."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_wallet import morpho_monitor  # noqa: E402

ADDRESS = "0xAbC0000000000000000000000000000000000001"
START = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _report(*moves: tuple[int, str, str], failed_chain: int | None = None) -> dict:
    by_chain: dict[int, list] = {}
    for chain_id, source, target in moves:
        by_chain.setdefault(chain_id, []).append(
            {
                "chainId": chain_id,
                "current": {"vault": {"address": source}},
                "candidates": [{"vault": {"address": target}}],
            }
        )
    reports = [{"chainId": chain_id, "error": None, "positions": positions} for chain_id, positions in by_chain.items()]
    if failed_chain is not None:
        reports.append({"chainId": failed_chain, "error": "Morpho API request failed", "positions": []})
    return {"address": ADDRESS, "networkReports": reports}


def _candidates(report: dict) -> list[dict]:
    return [
        candidate
        for network_report in report["networkReports"]
        for position in network_report["positions"]
        for candidate in position["candidates"]
    ]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "defi_monitor" / "morpho_rebalance.json"

        status = morpho_monitor.scan_status(ADDRESS, path=path, now=START)
        assert status["scan_due"] is True and status["last_scan_at"] is None, "first scan is always due"

        first = _report((8453, "0xA", "0xB"), (1, "0xC", "0xD"))
        meta = morpho_monitor.record_report(first, path=path, now=START)
        assert meta["new_opportunity_count"] == 2
        assert all(candidate["isNew"] for candidate in _candidates(first))
        assert meta["previous_scan_at"] is None

        status = morpho_monitor.scan_status(ADDRESS.lower(), path=path, now=START + timedelta(days=2))
        assert status["scan_due"] is False, "scan is not due before 3 days"
        assert status["last_scan_at"] == "2026-10-15T12:00:00Z"
        assert morpho_monitor.scan_status(ADDRESS, path=path, now=START + timedelta(days=3))["scan_due"] is True

        # Same moves again: nothing new. Ethereum fails: its known move is kept, not resurfaced later.
        second = _report((8453, "0xA", "0xB"), (8453, "0xA", "0xE"), failed_chain=1)
        meta = morpho_monitor.record_report(second, path=path, now=START + timedelta(days=3))
        flags = {c["vault"]["address"]: c["isNew"] for c in _candidates(second)}
        assert flags == {"0xB": False, "0xE": True}, flags
        assert meta["new_opportunity_count"] == 1
        assert meta["previous_scan_at"] == "2026-10-15T12:00:00Z"

        third = _report((1, "0xC", "0xD"))
        morpho_monitor.record_report(third, path=path, now=START + timedelta(days=6))
        assert _candidates(third)[0]["isNew"] is False, "a move kept through an outage is not new again"

        # A move that disappeared and came back is new again.
        fourth = _report((8453, "0xA", "0xB"))
        morpho_monitor.record_report(fourth, path=path, now=START + timedelta(days=9))
        assert _candidates(fourth)[0]["isNew"] is True

        path.write_text("{not json", encoding="utf-8")
        assert morpho_monitor.scan_status(ADDRESS, path=path, now=START)["scan_due"] is True, "corrupt state is ignored"

    _check_schedule_recipes()
    print("smoke_morpho_monitor: ok")


def _check_schedule_recipes() -> None:
    prompt = morpho_monitor.MONITOR_PROMPT
    assert "get_morpho_rebalance_report" in prompt
    assert "Do not call any wallet write tool" in prompt, "scheduled runs must stay read-only"

    hermes = morpho_monitor.schedule_recipe("hermes")
    call = hermes["agent_tool_call"]
    assert call["tool"] == "cronjob" and call["arguments"]["action"] == "create"
    assert call["arguments"]["schedule"] == "every 72h"
    assert call["arguments"]["toolsets"] == ["agent_wallet"]
    assert "deliver" not in call["arguments"], "omitting deliver keeps results in the originating chat"
    assert hermes["cli_command"][:4] == ["hermes", "cron", "create", "every 72h"]

    openclaw = morpho_monitor.schedule_recipe("openclaw")
    assert openclaw["job"] == {
        "name": morpho_monitor.JOB_NAME,
        "every": "72h",
        "session": "isolated",
        "message": prompt,
        "announce": True,
    }
    assert openclaw["cli_command"][:3] == ["openclaw", "cron", "add"]

    assert morpho_monitor.schedule_recipe("claude-code")["task_prompt"] == "/wallet:morpho-monitor"
    assert morpho_monitor.schedule_recipe("codex")["task_prompt"] == "$morpho-monitor"
    for host in morpho_monitor.SCHEDULE_HOSTS:
        recipe = morpho_monitor.schedule_recipe(host)
        assert recipe["interval_days"] == 3 and recipe["requires_user_consent"] is True
    try:
        morpho_monitor.schedule_recipe("cursor")
    except ValueError as exc:
        assert "host must be one of" in str(exc)
    else:
        raise AssertionError("unknown hosts must be rejected")


if __name__ == "__main__":
    main()
