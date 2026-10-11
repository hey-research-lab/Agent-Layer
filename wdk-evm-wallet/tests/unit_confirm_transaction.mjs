import assert from "node:assert/strict";
import test from "node:test";

import { __testables } from "../src/wdk_evm_wallet.js";

const { confirmTransaction, setRpcRequestForTests } = __testables;

const RUNTIME_CONFIG = { providerUrl: "http://example.invalid", network: "base" };

test("confirmTransaction returns confirmed when the receipt lands with status 0x1", async () => {
  setRpcRequestForTests(async () => ({ status: "0x1", blockNumber: "0x10" }));
  try {
    const result = await confirmTransaction(RUNTIME_CONFIG, "0xabc", { maxWaitMs: 500 });
    assert.deepEqual(result, { status: "confirmed", receipt: { status: "0x1", blockNumber: "0x10" } });
  } finally {
    setRpcRequestForTests(null);
  }
});

test("confirmTransaction throws a tagged error when the receipt shows a revert", async () => {
  setRpcRequestForTests(async () => ({ status: "0x0" }));
  try {
    await assert.rejects(
      () => confirmTransaction(RUNTIME_CONFIG, "0xabc", { operationLabel: "Test op", failureCode: "test_reverted", maxWaitMs: 500 }),
      (error) => {
        assert.equal(error.errorCode, "test_reverted");
        assert.equal(error.errorDetails.txHash, "0xabc");
        return true;
      }
    );
  } finally {
    setRpcRequestForTests(null);
  }
});

test("confirmTransaction returns submitted when the wait window elapses with clean but empty responses", async () => {
  setRpcRequestForTests(async () => null);
  try {
    const result = await confirmTransaction(RUNTIME_CONFIG, "0xabc", { maxWaitMs: 1200 });
    assert.deepEqual(result, { status: "submitted" });
  } finally {
    setRpcRequestForTests(null);
  }
});

test("confirmTransaction returns unknown when every poll attempt errors", async () => {
  setRpcRequestForTests(async () => {
    throw new Error("connect ECONNREFUSED");
  });
  try {
    const result = await confirmTransaction(RUNTIME_CONFIG, "0xabc", { maxWaitMs: 1200 });
    assert.deepEqual(result, { status: "unknown" });
  } finally {
    setRpcRequestForTests(null);
  }
});

test("confirmTransaction recovers to submitted if a later poll succeeds after early errors", async () => {
  let calls = 0;
  setRpcRequestForTests(async () => {
    calls += 1;
    if (calls === 1) throw new Error("transient");
    return null;
  });
  try {
    const result = await confirmTransaction(RUNTIME_CONFIG, "0xabc", { maxWaitMs: 1200 });
    assert.deepEqual(result, { status: "submitted" });
  } finally {
    setRpcRequestForTests(null);
  }
});

test("a submitted (not yet confirmed) result still carries a usable tx_hash and status", async () => {
  setRpcRequestForTests(async () => null);
  try {
    const confirmation = await confirmTransaction(RUNTIME_CONFIG, "0xdeadbeef", { maxWaitMs: 500 });
    const merged = {
      result: { hash: "0xdeadbeef" },
      confirmed: confirmation.status === "confirmed",
      tx_hash: "0xdeadbeef",
      confirmation_status: confirmation.status,
    };
    assert.equal(merged.confirmed, false);
    assert.equal(merged.tx_hash, "0xdeadbeef");
    assert.equal(merged.confirmation_status, "submitted");
  } finally {
    setRpcRequestForTests(null);
  }
});

test("a confirmed transfer merges confirmation_status into its response shape", async () => {
  setRpcRequestForTests(async () => ({ status: "0x1" }));
  try {
    const confirmation = await confirmTransaction(RUNTIME_CONFIG, "0xtransfer", {
      operationLabel: "Native transfer",
      failureCode: "native_transfer_reverted",
      maxWaitMs: 500,
    });
    assert.equal(confirmation.status, "confirmed");
  } finally {
    setRpcRequestForTests(null);
  }
});

test("confirmTransaction with no options defaults to transaction_reverted on revert (the shape internal cleanup/recovery loops rely on)", async () => {
  setRpcRequestForTests(async () => ({ status: "0x0" }));
  try {
    await assert.rejects(
      () => confirmTransaction(RUNTIME_CONFIG, "0xabc"),
      (error) => {
        assert.equal(error.errorCode, "transaction_reverted");
        return true;
      }
    );
  } finally {
    setRpcRequestForTests(null);
  }
});

test("a reverted transfer throws native_transfer_reverted, not a generic error", async () => {
  setRpcRequestForTests(async () => ({ status: "0x0" }));
  try {
    await assert.rejects(
      () =>
        confirmTransaction(RUNTIME_CONFIG, "0xtransfer", {
          operationLabel: "Native transfer",
          failureCode: "native_transfer_reverted",
          maxWaitMs: 500,
        }),
      (error) => {
        assert.equal(error.errorCode, "native_transfer_reverted");
        return true;
      }
    );
  } finally {
    setRpcRequestForTests(null);
  }
});

test("resolveTransactionStatus reports pending_or_unknown when the pending lookup is refused", async () => {
  const { resolveTransactionStatus } = __testables;
  setRpcRequestForTests(async (_url, method) => {
    if (method === "eth_getTransactionReceipt") return null;
    throw new Error("RPC request failed with HTTP 403.");
  });
  try {
    const result = await resolveTransactionStatus("http://example.invalid", "0xabc");
    assert.equal(result.status, "pending_or_unknown");
    assert.equal(result.receipt, null);
    assert.match(result.note, /HTTP 403/);
  } finally {
    setRpcRequestForTests(null);
  }
});

test("resolveTransactionStatus distinguishes confirmed, reverted, pending and not_found", async () => {
  const { resolveTransactionStatus } = __testables;
  const cases = [
    [{ status: "0x1" }, null, "confirmed"],
    [{ status: "0x0" }, null, "reverted"],
    [null, { hash: "0xabc" }, "pending"],
    [null, null, "not_found"],
  ];
  for (const [receipt, tx, expected] of cases) {
    setRpcRequestForTests(async (_url, method) => (method === "eth_getTransactionReceipt" ? receipt : tx));
    try {
      assert.equal((await resolveTransactionStatus("http://example.invalid", "0xabc")).status, expected);
    } finally {
      setRpcRequestForTests(null);
    }
  }
});
