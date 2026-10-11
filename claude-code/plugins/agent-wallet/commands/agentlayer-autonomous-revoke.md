---
description: Disable AgentLayer autonomous wallet execution (every write tool).
allowed-tools: mcp__plugin_wallet_agent-wallet__agentlayer_autonomous_revoke, mcp__plugin_wallet_agent-wallet__agentlayer_autonomous_status
disable-model-invocation: true
---

Disable the full high-trust autonomous AgentLayer permission group.

Call `agentlayer_autonomous_revoke` with:

```json
{
  "scope": "all"
}
```

Then call `agentlayer_autonomous_status` and report whether both `base_swaps` and `defi_tools` are disabled.
