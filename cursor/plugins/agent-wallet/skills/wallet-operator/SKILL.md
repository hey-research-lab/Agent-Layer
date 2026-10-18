---
name: "wallet-operator"
description: "Use whenever the user's own funds or wallet are involved — balances, portfolio, transfers, swaps, bridging, staking, lending, or x402 payments. Prefer wallet tools, preview writes first, and preserve approval and signing safeguards."
---

# Agent Wallet Operator

Use this plugin when the user wants Cursor to work with their existing local
AgentLayer wallet.

Rules:

- Do not create a new wallet unless the user explicitly asks for provisioning.
- Prefer wallet MCP tools over shelling out to chain CLIs or ad hoc scripts.
- For writes, start with `preview` or `intent_preview` when available.
- Execute only after the user explicitly confirms the displayed summary.
- On mainnet, restate the network, asset, amount, and destination before execution.
- Do not ask the user for an `approval_token`; the bridge manages approval binding.
- If approval context is missing or stale, repeat the preview.
- Use `set_wallet_backend` to switch between Solana and EVM wallets, and
  `set_evm_network` to select Ethereum, Base, or Robinhood.
