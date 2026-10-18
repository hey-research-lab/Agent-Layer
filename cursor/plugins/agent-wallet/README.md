# Agent Wallet for Cursor

This Cursor plugin connects to the existing AgentLayer wallet runtime. It does
not create a wallet or store private keys in Cursor plugin settings. Wallet
policy, approvals, and signing stay in the shared Python runtime.

## Install automatically

The universal installer detects Cursor and installs this plugin with the shared
runtime:

```bash
npx --yes @agentlayer.tech/wallet@latest install --yes
```

If Cursor was installed after AgentLayer, run the same command again. The
installer detects and adds Cursor. To select only Cursor, use:

```bash
npx --yes @agentlayer.tech/wallet@latest install --yes --hosts cursor
```

The installer copies the plugin to `~/.cursor/plugins/local/agent-wallet` and
records it as a managed integration. `wallet update --yes` refreshes that copy.
In Cursor, run **Developer: Reload Window**, then check Customize → Plugins
and MCP. The `agent-wallet` MCP server should start from the plugin's stdio
bridge. On Teams or Enterprise, an admin may need to enable local plugin
imports.

Alternatively, add the AgentLayer marketplace in Cursor's Plugins view using
the public repository `lopushok9/Agent-Layer`, then install `agent-wallet`.
This marketplace route becomes available after the repository update is pushed
to GitHub.

## Runtime and wallet setup

Node.js/npm and Python 3 are needed for the runtime installation. The bridge
looks for the runtime at
`~/.openclaw/agent-wallet-runtime/current/agent-wallet`. To use another path,
set `AGENT_WALLET_PACKAGE_ROOT` or `OPENCLAW_AGENT_WALLET_PACKAGE_ROOT` in the
Cursor MCP server environment.

The plugin reuses the wallet and credentials already configured for
AgentLayer/OpenClaw. It does not ask for keys in Cursor. Once connected, use
the wallet tools for reads; preview write operations and execute only after
reviewing and confirming the transaction summary.

## Included skills

- `wallet-operator` — wallet task routing and transaction safety rules
- `wallet-sol` — Solana wallet overview
- `wallet-base` — Base EVM overview and session selection
- `x402` — discover, preview, and pay for x402 services
- `cards` — issue a prepaid card through the connected wallet

## Grok Bot

Grok Bot does not document importing Cursor's local plugin directory. The
officially documented route for Grok Bot is to install a supported listing via
Connect apps. Grok can also connect to a custom MCP server only when its URL is
reachable from the public internet; a local stdio process is not such a server.
See the root README for the current connection options.
