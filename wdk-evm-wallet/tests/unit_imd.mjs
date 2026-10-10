import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { Wallet, verifyTypedData } from "ethers";

import {
  IMD_TOKEN_ADDRESS,
  IMD_PAY_TO_ALLOWLIST,
  X402_EXACT_PERMIT2_PROXY_ADDRESS,
  buildImdPermit2TypedData,
  buildImdQuoteApprovalTypedData,
  normalizeImdAllowanceAmount,
} from "../src/imd.js";

const NOW = 1_790_000_000;
const MAINNET = { chainId: 1, network: "ethereum" };
const PAY_TO = IMD_PAY_TO_ALLOWLIST[0];
const HALF_IMD = "500000000000000000";

function permitFixture(overrides = {}) {
  return {
    permitted: { token: IMD_TOKEN_ADDRESS, amount: HALF_IMD },
    spender: X402_EXACT_PERMIT2_PROXY_ADDRESS,
    nonce: "123456789",
    deadline: String(NOW + 300),
    witness: { to: PAY_TO, validAfter: String(NOW - 600) },
    ...overrides,
  };
}

function approvalFixture(overrides = {}) {
  return {
    resource: "https://api.imd.fun/requests/ORDER_ID/submit",
    requesterScopeHash: `0x${"11".repeat(32)}`,
    quoteId: "ORDER_ID",
    quoteHash: `0x${"22".repeat(32)}`,
    paymentHash: `0x${"33".repeat(32)}`,
    action: "job.open",
    asset: IMD_TOKEN_ADDRESS,
    amount: HALF_IMD,
    payTo: PAY_TO,
    expiresAt: String(NOW + 500),
    ...overrides,
  };
}

test("permit2 typed data signs and recovers the signer", async () => {
  const wallet = Wallet.createRandom();
  const typed = buildImdPermit2TypedData(permitFixture(), MAINNET, NOW);
  assert.equal(typed.primaryType, "PermitWitnessTransferFrom");
  assert.deepEqual(typed.domain, {
    name: "Permit2",
    chainId: 1,
    verifyingContract: "0x000000000022D473030F116dDEE9F6B43aC78BA3",
  });
  const signature = await wallet.signTypedData(typed.domain, typed.types, typed.message);
  assert.equal(verifyTypedData(typed.domain, typed.types, typed.message, signature), wallet.address);
});

test("quote approval typed data signs and recovers the signer", async () => {
  const wallet = Wallet.createRandom();
  const typed = buildImdQuoteApprovalTypedData(approvalFixture(), MAINNET, NOW);
  assert.deepEqual(typed.domain, { name: "IdentityMD Paid Action", version: "1", chainId: 1 });
  const signature = await wallet.signTypedData(typed.domain, typed.types, typed.message);
  assert.match(signature, /^0x[0-9a-f]{130}$/);
  assert.equal(verifyTypedData(typed.domain, typed.types, typed.message, signature), wallet.address);
});

test("permit2 refuses anything outside the IMD allowlist", () => {
  const cases = [
    [{ permitted: { token: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", amount: HALF_IMD } }, /IMD token/],
    [{ spender: "0x4020A4f3b7b90ccA423B9fabCc0CE57C6C240002" }, /Permit2 proxy/],
    [{ witness: { to: "0x1111111111111111111111111111111111111111", validAfter: "0" } }, /treasury/],
    [{ permitted: { token: IMD_TOKEN_ADDRESS, amount: "5000000000000000001" } }, /ceiling/],
    [{ permitted: { token: IMD_TOKEN_ADDRESS, amount: "0" } }, /greater than zero/],
    [{ deadline: String(NOW - 1) }, /past/],
    [{ deadline: String(NOW + 3600) }, /in the future/],
  ];
  for (const [overrides, pattern] of cases) {
    assert.throws(() => buildImdPermit2TypedData(permitFixture(overrides), MAINNET, NOW), pattern);
  }
});

test("both signatures refuse networks other than Ethereum mainnet", () => {
  const base = { chainId: 8453, network: "base" };
  assert.throws(() => buildImdPermit2TypedData(permitFixture(), base, NOW), /Ethereum mainnet/);
  assert.throws(() => buildImdQuoteApprovalTypedData(approvalFixture(), base, NOW), /Ethereum mainnet/);
});

test("quote approval refuses foreign resources, actions and recipients", () => {
  const cases = [
    [{ resource: "https://evil.example/requests/x/submit" }, /api\.imd\.fun/],
    [{ resource: "https://api.imd.fun.evil.example/x" }, /api\.imd\.fun/],
    [{ action: "transfer" }, /not an IMD paid action/],
    [{ payTo: "0x1111111111111111111111111111111111111111" }, /treasury/],
    [{ asset: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" }, /IMD token/],
    [{ paymentHash: "0x1234" }, /32 bytes/],
    [{ expiresAt: String(NOW + 3600) }, /in the future/],
  ];
  for (const [overrides, pattern] of cases) {
    assert.throws(() => buildImdQuoteApprovalTypedData(approvalFixture(overrides), MAINNET, NOW), pattern);
  }
});

test("allowance amount is bounded by the per-payment ceiling", () => {
  assert.equal(normalizeImdAllowanceAmount(HALF_IMD), 500000000000000000n);
  assert.throws(() => normalizeImdAllowanceAmount("6000000000000000000"), /ceiling/);
  assert.throws(() => normalizeImdAllowanceAmount("-1"), /integer/);
});

// IMD's reference canonical JSON (docs, "the quote approval"). The Python
// provider pins the same fixture and digest, so both sides hash identically.
const canon = (v) =>
  v === null
    ? "null"
    : Array.isArray(v)
      ? `[${v.map(canon).join(",")}]`
      : typeof v === "object"
        ? `{${Object.keys(v)
            .sort()
            .map((k) => `${JSON.stringify(k)}:${canon(v[k])}`)
            .join(",")}}`
        : JSON.stringify(v);

export const PAYMENT_HASH_FIXTURE = {
  x402Version: 2,
  resource: { url: "https://api.imd.fun/requests/ORDER_ID/submit", description: "IMD job.open — ü", mimeType: "application/json" },
  accepted: {
    scheme: "exact",
    network: "eip155:1",
    asset: IMD_TOKEN_ADDRESS,
    amount: HALF_IMD,
    payTo: PAY_TO,
    maxTimeoutSeconds: 300,
    extra: { assetTransferMethod: "permit2" },
  },
  payload: {
    signature: `0x${"ab".repeat(65)}`,
    permit2Authorization: {
      from: "0xDF7eD8B45ae91a0881c83A876747AF1FfB48C36E",
      permitted: { token: IMD_TOKEN_ADDRESS, amount: HALF_IMD },
      spender: X402_EXACT_PERMIT2_PROXY_ADDRESS,
      nonce: "123456789",
      deadline: "1790000300",
      witness: { to: PAY_TO, validAfter: "1789999400" },
    },
  },
};

test("canonical payment hash matches the pinned cross-language digest", () => {
  const digest = createHash("sha256").update(canon(PAYMENT_HASH_FIXTURE), "utf8").digest("hex");
  assert.equal(digest, "90b1544310eb9f669a9082d03441fb4badf33534d5c941e48562488f458e9a4f");
});
