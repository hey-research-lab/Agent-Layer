"""Local state for the Morpho rebalance monitor.

The scan itself runs in the EVM wallet daemon; this module only remembers, per
wallet address, when the last scan ran and which move recommendations it
returned. That lets every host (Claude Code, Codex, Hermes, OpenClaw) mark a
recommendation as new or already seen, and tell the agent when a scan is due.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import resolve_openclaw_home
from .file_ops import atomic_write_text

SCAN_INTERVAL_DAYS = 3
STATE_VERSION = 1


def state_path() -> Path:
    return resolve_openclaw_home() / "defi_monitor" / "morpho_rebalance.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "wallets": {}}
    if not isinstance(data, dict) or not isinstance(data.get("wallets"), dict):
        return {"version": STATE_VERSION, "wallets": {}}
    return data


def _wallet_key(address: str | None) -> str:
    return str(address or "").strip().lower()


def _recommendation_key(position: dict[str, Any], candidate: dict[str, Any]) -> str:
    chain_id = position.get("chainId")
    source = str((position.get("current") or {}).get("vault", {}).get("address") or "").lower()
    target = str((candidate.get("vault") or {}).get("address") or "").lower()
    return f"{chain_id}:{source}->{target}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def scan_status(address: str | None, *, path: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """When the last scan ran for this wallet and whether the next one is due."""
    current = now or _now()
    wallet = _load_state(path or state_path())["wallets"].get(_wallet_key(address)) or {}
    last_scan_at = wallet.get("last_scan_at")
    last = _parse_time(last_scan_at)
    due = last is None or current - last >= timedelta(days=SCAN_INTERVAL_DAYS)
    return {
        "last_scan_at": last_scan_at if last else None,
        "scan_due": due,
        "interval_days": SCAN_INTERVAL_DAYS,
    }


def record_report(
    report: dict[str, Any],
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Mark each candidate `isNew` against the previous scan and persist this scan.

    A recommendation is new when the previous scan for the same wallet did not
    return the same source -> target move. Network reports that failed are left
    out of the comparison so a transient outage does not resurface old moves.
    """
    target = path or state_path()
    current = now or _now()
    state = _load_state(target)
    key = _wallet_key(report.get("address"))
    previous = state["wallets"].get(key) or {}
    seen = set(previous.get("recommendations") or [])
    keys: list[str] = []
    new_count = 0
    for network_report in report.get("networkReports") or []:
        if network_report.get("error"):
            # Keep the moves we already knew about on that network until it answers again.
            chain_prefix = f"{network_report.get('chainId')}:" if network_report.get("chainId") else None
            if chain_prefix:
                keys.extend(item for item in seen if item.startswith(chain_prefix))
            continue
        for position in network_report.get("positions") or []:
            for candidate in position.get("candidates") or []:
                rec_key = _recommendation_key(position, candidate)
                is_new = rec_key not in seen
                candidate["isNew"] = is_new
                new_count += int(is_new)
                keys.append(rec_key)
    if key:
        state["wallets"][key] = {
            "last_scan_at": current.isoformat().replace("+00:00", "Z"),
            "recommendations": sorted(set(keys)),
        }
        state["version"] = STATE_VERSION
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(state, indent=2, sort_keys=True))
    return {
        "previous_scan_at": previous.get("last_scan_at"),
        "new_opportunity_count": new_count,
        "next_scan_due_at": (current + timedelta(days=SCAN_INTERVAL_DAYS)).isoformat().replace("+00:00", "Z"),
        "interval_days": SCAN_INTERVAL_DAYS,
    }


JOB_NAME = "agentlayer-morpho-monitor"
SCHEDULE_HOSTS = ("claude_code", "codex", "openclaw", "hermes")

# The one instruction every scheduled run executes, whatever the host. It only reads;
# moving funds always starts a separate, user-confirmed flow.
MONITOR_PROMPT = (
    "AgentLayer Morpho monitor run (read-only). Call the AgentLayer wallet tool "
    "get_morpho_rebalance_report with no arguments. Do not call any wallet write tool in this run. "
    "If monitor.new_opportunity_count is 0, reply with one short line: no new better Morpho vaults "
    "for the wallet, with the number of positions checked. Otherwise, for each position that has a "
    "candidate with isNew true, show the current vault (current rate, 7-day and 30-day averages, "
    "position size) and each new candidate (7-day and 30-day averages, difference versus the "
    "current vault, estimatedYearlyGainUsd, reward share, TVL and positionShareOfTvl, warnings, "
    "young flag), then the estimated migration cost, labelled as an estimate. State plainly that "
    "nothing was moved. End by asking whether the user wants to move a position; a move would use "
    "rebalance_evm_morpho_vault_position, starting with intent_preview and executing only after "
    "the user explicitly confirms."
)


def schedule_recipe(host: str) -> dict[str, Any]:
    """How to run MONITOR_PROMPT every SCAN_INTERVAL_DAYS on a given agent host."""
    normalized = str(host or "").strip().lower().replace("-", "_")
    if normalized not in SCHEDULE_HOSTS:
        raise ValueError(f"host must be one of: {', '.join(SCHEDULE_HOSTS)}.")
    hours = SCAN_INTERVAL_DAYS * 24
    recipe: dict[str, Any] = {
        "host": normalized,
        "job_name": JOB_NAME,
        "interval_days": SCAN_INTERVAL_DAYS,
        "prompt": MONITOR_PROMPT,
        "requires_user_consent": True,
        "note": (
            "Create the schedule only after the user asks for monitoring. Each run is read-only; "
            "it never moves funds."
        ),
    }
    if normalized == "hermes":
        recipe["method"] = "agent_cron_tool"
        recipe["agent_tool_call"] = {
            "tool": "cronjob",
            "arguments": {
                "action": "create",
                "name": JOB_NAME,
                "schedule": f"every {hours}h",
                "prompt": MONITOR_PROMPT
                + " In Hermes the wallet tools are called through agent_wallet_invoke.",
                "toolsets": ["agent_wallet"],
            },
        }
        recipe["cli_command"] = [
            "hermes", "cron", "create", f"every {hours}h", MONITOR_PROMPT, "--name", JOB_NAME,
        ]
        recipe["delivery"] = "Omitting deliver sends each result back to the chat where the job was created."
    elif normalized == "openclaw":
        recipe["method"] = "agent_cron_tool"
        recipe["agent_tool"] = "cron"
        recipe["job"] = {
            "name": JOB_NAME,
            "every": f"{hours}h",
            "session": "isolated",
            "message": MONITOR_PROMPT,
            "announce": True,
        }
        recipe["cli_command"] = [
            "openclaw", "cron", "add", "--name", JOB_NAME, "--every", f"{hours}h",
            "--session", "isolated", "--message", MONITOR_PROMPT, "--announce",
        ]
        recipe["delivery"] = "--announce delivers the run's final reply to the user's last chat channel."
    elif normalized == "claude_code":
        recipe["method"] = "desktop_scheduled_task"
        recipe["task_prompt"] = "/wallet:morpho-monitor"
        recipe["user_steps"] = [
            "Open the Claude desktop app and go to Scheduled tasks (Claude Code).",
            f"Create a task named {JOB_NAME} that runs every {SCAN_INTERVAL_DAYS} days in a folder where "
            "the AgentLayer wallet plugin is enabled.",
            "Use /wallet:morpho-monitor as the task prompt.",
        ]
        recipe["delivery"] = "The result appears in the task's session; desktop notifications follow app settings."
    else:
        recipe["method"] = "codex_automation"
        recipe["task_prompt"] = "$morpho-monitor"
        recipe["user_steps"] = [
            "Open the Codex app and go to Automations.",
            f"Create an automation named {JOB_NAME} that runs every {SCAN_INTERVAL_DAYS} days in a project "
            "where the AgentLayer wallet plugin is enabled.",
            "Use $morpho-monitor as the automation prompt.",
        ]
        recipe["delivery"] = "Each run's result appears in the Codex automations inbox."
    return recipe
