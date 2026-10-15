---
description: Check the wallet's Morpho vault positions on Base and Ethereum for better vaults, set up a scan every 3 days, or move a position after confirmation.
argument-hint: "[schedule | move <from_vault> <to_vault> [amount_raw]]"
allowed-tools: AskUserQuestion, mcp__plugin_wallet_agent-wallet__get_active_wallet_backend, mcp__plugin_wallet_agent-wallet__set_wallet_backend, mcp__plugin_wallet_agent-wallet__get_evm_morpho_positions, mcp__plugin_wallet_agent-wallet__get_evm_morpho_vaults, mcp__plugin_wallet_agent-wallet__get_morpho_rebalance_report, mcp__plugin_wallet_agent-wallet__get_morpho_monitor_schedule, mcp__plugin_wallet_agent-wallet__rebalance_evm_morpho_vault_position, mcp__plugin_wallet_agent-wallet__issue_wallet_approval
---

# Morpho monitor

Arguments: `$ARGUMENTS`

The monitor compares the wallet's Morpho vault positions (V1 and V2) on Base
and Ethereum with other vaults holding the same asset on the same chain. A
candidate is listed, has no red warning, clears a $1M TVL floor and beats the
current vault on both the realized 7-day and 30-day net APY. Migration gas is
an estimate shown to the user; it never removes a candidate.

## No arguments — run the scan

1. Call `get_morpho_rebalance_report` with no arguments (it reads only).
2. If `monitor.new_opportunity_count` is 0 and no position has candidates, say
   in one line that no better vaults were found and how many positions were
   checked.
3. Otherwise, per position: the current vault (current rate, 7d and 30d
   averages, position size), then each candidate in a compact table: 7d / 30d,
   difference versus the current vault, `estimatedYearlyGainUsd`, reward
   share, TVL and `positionShareOfTvl`, warnings, the young flag, and whether
   it is new since the last scan. Then the migration cost estimate.
4. Point out what deserves attention without deciding for the user: a large
   reward share, a position that would be a big share of a small vault, a
   young vault, a yellow warning.
5. Say plainly that nothing was moved, and ask whether the user wants to move
   a position.

## `schedule` — run the scan every 3 days

Call `get_morpho_monitor_schedule` with `host: "claude_code"` and give the user
the returned `user_steps`. If a scheduled-task tool is available in this
session, offer to create the task with the returned `task_prompt` and the
3-day interval, and create it only after the user agrees.

## `move` — move a position after confirmation

1. Call `rebalance_evm_morpho_vault_position` with `mode: "intent_preview"`,
   the two vault addresses, the network of the position, an optional
   `amount_raw` (omit it to move the whole position) and a short `purpose`.
2. Show the preview: both vaults with their rates, the amount, the withdraw
   gas estimate, whether an approval runs before the supply, and any
   `target_risk_flags`. Ask for explicit confirmation with `AskUserQuestion`.
3. Only after the user confirms, call `issue_wallet_approval` with the
   `confirmation_summary` exactly as returned, then call the same tool with
   `mode: "intent_execute"`, the same arguments and the `approval_token`.
4. Report each step's transaction hash. If `status` is `partial`, the funds
   are in the wallet: say so and do not retry on your own.

Never move funds from a scheduled run, and never reuse an approval for a
different vault or amount.
