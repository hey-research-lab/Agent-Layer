import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { syncCursorPluginBundle } from "./integrations.mjs";

test("Cursor install preserves an unmanaged plugin and refreshes its own copy", () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "agentlayer-cursor-install-"));
  try {
    const source = path.join(temp, "source");
    const target = path.join(temp, "cursor", "plugins", "local", "agent-wallet");
    fs.mkdirSync(path.join(source, ".cursor-plugin"), { recursive: true });
    fs.writeFileSync(path.join(source, ".cursor-plugin", "plugin.json"), '{"name":"agent-wallet"}\n');
    fs.writeFileSync(path.join(source, "mcp.json"), '{"mcpServers":{"agent-wallet":{"command":"sh","env":{}}}}\n');
    fs.writeFileSync(path.join(source, "server.py"), "first\n");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, "user-file.txt"), "keep\n");

    assert.throws(
      () => syncCursorPluginBundle({ source, target, packageVersion: "0.1.123" }),
      /not managed by AgentLayer/,
    );
    assert.equal(fs.readFileSync(path.join(target, "user-file.txt"), "utf8"), "keep\n");

    syncCursorPluginBundle({
      source,
      target,
      packageVersion: "0.1.123",
      force: true,
      runtimePackageRoot: "/custom/openclaw/agent-wallet-runtime/current/agent-wallet",
      openclawHome: "/custom/openclaw",
      defaultOpenclawHome: "/default/openclaw",
    });
    const config = JSON.parse(fs.readFileSync(path.join(target, "mcp.json"), "utf8"));
    assert.equal(config.mcpServers["agent-wallet"].command, "sh");
    assert.deepEqual(config.mcpServers["agent-wallet"].args, [path.join(target, "scripts", "run_mcp.sh")]);
    assert.equal(config.mcpServers["agent-wallet"].env.OPENCLAW_HOME, "/custom/openclaw");
    assert.equal(config.mcpServers["agent-wallet"].env.AGENT_WALLET_PACKAGE_ROOT,
      "/custom/openclaw/agent-wallet-runtime/current/agent-wallet");
    assert.equal(JSON.parse(fs.readFileSync(path.join(target, ".agentlayer-managed.json"))).manager, "agentlayer");

    fs.writeFileSync(path.join(source, "server.py"), "second\n");
    syncCursorPluginBundle({ source, target, packageVersion: "0.1.124" });
    assert.equal(fs.readFileSync(path.join(target, "server.py"), "utf8"), "second\n");
    assert.equal(JSON.parse(fs.readFileSync(path.join(target, ".agentlayer-managed.json"))).version, "0.1.124");
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
