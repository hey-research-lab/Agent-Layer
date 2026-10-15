import assert from "node:assert/strict";
import test from "node:test";

import { releaseCheckPython } from "./release-python.mjs";

const VENV = "/runtime/releases/0.1.121/agent-wallet/.venv/bin/python";

test("the staged release venv wins over every env override", () => {
  assert.equal(
    releaseCheckPython(VENV, {
      OPENCLAW_AGENT_WALLET_PYTHON: "/opt/homebrew/bin/python3.11",
      AGENT_WALLET_PYTHON: "/runtime/releases/0.1.120/agent-wallet/.venv/bin/python",
    }),
    VENV
  );
});

test("without a venv the env overrides are used, previous runtime first", () => {
  assert.equal(
    releaseCheckPython(null, { OPENCLAW_AGENT_WALLET_PYTHON: "/a", AGENT_WALLET_PYTHON: "/b" }),
    "/b"
  );
  assert.equal(releaseCheckPython(null, { OPENCLAW_AGENT_WALLET_PYTHON: "/a" }), "/a");
  assert.equal(releaseCheckPython(null, {}), null);
});
