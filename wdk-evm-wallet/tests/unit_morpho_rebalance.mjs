import assert from "node:assert/strict";
import test from "node:test";

import { __testables } from "../src/wdk_evm_wallet.js";

const { rankMorphoRebalanceCandidates } = __testables;

const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913";
const OTHER_ASSET = "0x0000000000000000000000000000000000000ee1";
const NOW = 1_800_000_000;
const OLD = String(NOW - 365 * 24 * 60 * 60);

const CURRENT = "0x00000000000000000000000000000000000000c0";

function position(overrides = {}) {
  return {
    version: "v1",
    vault: {
      address: CURRENT,
      name: "Current USDC",
      asset: { address: USDC, symbol: "USDC" },
      netApy: 0.035,
      avgNetApy7d: 0.035,
      avgNetApy30d: 0.033,
      avgNetApy7dExcludingRewards: 0.035,
      ...overrides,
    },
    assets: "100000000000",
    assetsUsd: 100_000,
  };
}

function vault(address, overrides = {}) {
  return {
    version: "v2",
    address,
    name: `Vault ${address.slice(-2)}`,
    listed: true,
    creationTimestamp: OLD,
    asset: { address: USDC },
    totalAssetsUsd: 50_000_000,
    netApy: 0.05,
    avgNetApy7d: 0.05,
    avgNetApy30d: 0.048,
    avgNetApy7dExcludingRewards: 0.04,
    warnings: [],
    underlyingVaults: [],
    ...overrides,
  };
}

function rank(vaults, extra = {}) {
  return rankMorphoRebalanceCandidates({
    position: extra.position || position(),
    currentVault: extra.currentVault || null,
    vaults,
    minTvlUsd: 1_000_000,
    candidateLimit: extra.candidateLimit ?? 3,
    nowSeconds: NOW,
  });
}

test("a vault that beats the current one on 7d and 30d is a candidate", () => {
  const { candidates } = rank([vault("0x00000000000000000000000000000000000000a1")]);
  assert.equal(candidates.length, 1);
  const [candidate] = candidates;
  assert.ok(Math.abs(candidate.delta7d - 0.015) < 1e-12);
  assert.ok(Math.abs(candidate.delta30d - 0.015) < 1e-12);
  assert.ok(Math.abs(candidate.estimatedYearlyGainUsd - 1500) < 1e-6);
  assert.ok(Math.abs(candidate.rewardShare7d - 0.2) < 1e-12);
  assert.ok(Math.abs(candidate.positionShareOfTvl - 100_000 / 50_100_000) < 1e-12);
  assert.equal(candidate.young, false);
});

test("a vault ahead on 7d but behind on 30d is rejected", () => {
  const { candidates } = rank([
    vault("0x00000000000000000000000000000000000000a1", { avgNetApy7d: 0.06, avgNetApy30d: 0.03 }),
  ]);
  assert.equal(candidates.length, 0);
});

test("a vault that is not better on 7d is rejected", () => {
  const { candidates } = rank([vault("0x00000000000000000000000000000000000000a1", { avgNetApy7d: 0.035 })]);
  assert.equal(candidates.length, 0);
});

test("hard filters: other asset, unlisted, red warning, low TVL, the current vault itself", () => {
  const { candidates } = rank([
    vault("0x00000000000000000000000000000000000000a1", { asset: { address: OTHER_ASSET } }),
    vault("0x00000000000000000000000000000000000000a2", { listed: false }),
    vault("0x00000000000000000000000000000000000000a3", { warnings: [{ type: "x", level: "RED" }] }),
    vault("0x00000000000000000000000000000000000000a4", { totalAssetsUsd: 999_999 }),
    vault(CURRENT),
  ]);
  assert.equal(candidates.length, 0);
});

test("a yellow warning is shown, not filtered", () => {
  const { candidates } = rank([
    vault("0x00000000000000000000000000000000000000a1", { warnings: [{ type: "x", level: "YELLOW" }] }),
  ]);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].warnings[0].level, "YELLOW");
});

test("a V2 vault wrapping the current V1 vault is not offered", () => {
  const { candidates } = rank([
    vault("0x00000000000000000000000000000000000000a1", {
      underlyingVaults: [{ address: CURRENT, version: "v1" }],
    }),
  ]);
  assert.equal(candidates.length, 0);
});

test("a vault without 30d history is compared on 7d and flagged young", () => {
  const { candidates } = rank([
    vault("0x00000000000000000000000000000000000000a1", {
      avgNetApy30d: null,
      creationTimestamp: String(NOW - 5 * 24 * 60 * 60),
    }),
  ]);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].young, true);
  assert.equal(candidates[0].delta30d, null);
});

test("candidates are ordered by 7d average and capped", () => {
  const { candidates } = rank(
    [
      vault("0x00000000000000000000000000000000000000a1", { avgNetApy7d: 0.04 }),
      vault("0x00000000000000000000000000000000000000a2", { avgNetApy7d: 0.06 }),
      vault("0x00000000000000000000000000000000000000a3", { avgNetApy7d: 0.05 }),
    ],
    { candidateLimit: 2 }
  );
  assert.deepEqual(
    candidates.map((candidate) => candidate.avgNetApy7d),
    [0.06, 0.05]
  );
});

test("a position whose vault has no 7d average is skipped with a reason", () => {
  const result = rank([vault("0x00000000000000000000000000000000000000a1")], {
    position: position({ avgNetApy7d: null }),
  });
  assert.equal(result.candidates.length, 0);
  assert.equal(result.skippedReason, "current_vault_has_no_7d_average");
});
