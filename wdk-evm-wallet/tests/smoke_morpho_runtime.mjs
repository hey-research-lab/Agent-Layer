import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import MorphoProtocolEvm from "@morpho-org/wdk-protocol-lending-morpho-evm";
import WalletManagerEvm from "@tetherto/wdk-wallet-evm";

import { WdkEvmWalletService } from "../src/wdk_evm_wallet.js";

const SEED_PHRASE =
  "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about";

function tempDataDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "wdk-evm-morpho-idempotency-"));
}

const DEFAULT_ADDRESS = "0x1111111111111111111111111111111111111111";
const DEFAULT_MARKET_ID =
  "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836";
const DEFAULT_VAULT_ADDRESS = "0xb576765fB15505433aF24FEe2c0325895C559FB2";
const DEFAULT_TOKEN = "0x2222222222222222222222222222222222222222";
const APPROVAL_SPENDER = "0x3333333333333333333333333333333333333333";
const MORPHO_CORE = "0x4444444444444444444444444444444444444444";
const GENERAL_ADAPTER = "0x5555555555555555555555555555555555555555";
const APPROVAL_DATA = "0xaaaaaaaa";
const AUTHORIZATION_DATA = "0xbbbbbbbb";
const MORPHO_OPERATION_DATA = "0xcccccccc";

function createService({ dataDir, network = "base" } = {}) {
  return new WdkEvmWalletService({
    network,
    ...(dataDir ? { dataDir } : {}),
    morphoApiBaseUrl: "https://morpho-api.test/graphql",
    networkProfiles: {
      ethereum: {
        chainId: 1,
        providerUrl: "http://fake-rpc.local",
        nativeSymbol: "ETH",
      },
      base: {
        chainId: 8453,
        providerUrl: "http://fake-rpc.local",
        nativeSymbol: "ETH",
      },
      arc: {
        chainId: 5042,
        providerUrl: "http://fake-rpc.local",
        nativeSymbol: "USDC",
      },
    },
  });
}

function withMockedFetch(handler, callback) {
  const original = globalThis.fetch;
  globalThis.fetch = handler;
  return Promise.resolve()
    .then(callback)
    .finally(() => {
      globalThis.fetch = original;
    });
}

function createMorphoHarness(options = {}) {
  const state = {
    allowance: BigInt(options.initialAllowance ?? 0n),
    approvalSatisfied: Boolean(options.initialApprovalSatisfied),
    authorizationSatisfied: Boolean(options.initialAuthorizationSatisfied),
    quoteCalls: [],
    protocolCalls: [],
    sentTransactions: [],
    operationHash: null,
  };

  const fakeAccount = {
    _config: {
      provider: {
        async request({ method }) {
          if (method === "eth_chainId") {
            return "0x2105";
          }
          if (method === "net_version") {
            return "8453";
          }
          return "0x1";
        },
      },
    },
    async getAddress() {
      return DEFAULT_ADDRESS;
    },
    async getAllowance() {
      return state.allowance;
    },
    async quoteSendTransaction(tx) {
      state.quoteCalls.push(tx);
      if (String(tx?.data || "") === AUTHORIZATION_DATA) {
        return { fee: BigInt(options.authorizationFee ?? 5n) };
      }
      return { fee: BigInt(options.approvalFee ?? 3n) };
    },
    async sendTransaction(tx) {
      state.sentTransactions.push(tx);
      if (String(tx?.data || "") === APPROVAL_DATA) {
        state.approvalSatisfied = true;
        state.allowance = BigInt(options.requiredAmount ?? 1_000_000n);
      }
      if (String(tx?.data || "") === AUTHORIZATION_DATA) {
        state.authorizationSatisfied = true;
      }
      const hash = `0x${String(state.sentTransactions.length).padStart(64, "a")}`;
      if (String(tx?.data || "") === MORPHO_OPERATION_DATA) {
        state.operationHash = hash;
      }
      return {
        hash,
        fee:
          String(tx?.data || "") === AUTHORIZATION_DATA
            ? BigInt(options.authorizationFee ?? 5n)
            : BigInt(options.approvalFee ?? 3n),
      };
    },
  };

  const originals = {
    getAccount: WalletManagerEvm.prototype.getAccount,
    disposeWallet: WalletManagerEvm.prototype.dispose,
    getVaultAddress: MorphoProtocolEvm.prototype.getVaultAddress,
    getBorrowMarketId: MorphoProtocolEvm.prototype.getBorrowMarketId,
    getSupplyRequirements: MorphoProtocolEvm.prototype.getSupplyRequirements,
    quoteSupply: MorphoProtocolEvm.prototype.quoteSupply,
    supply: MorphoProtocolEvm.prototype.supply,
    getBorrowRequirements: MorphoProtocolEvm.prototype.getBorrowRequirements,
    quoteBorrow: MorphoProtocolEvm.prototype.quoteBorrow,
    borrow: MorphoProtocolEvm.prototype.borrow,
    fetch: globalThis.fetch,
  };

  WalletManagerEvm.prototype.getAccount = async function getAccount() {
    return fakeAccount;
  };
  WalletManagerEvm.prototype.dispose = function dispose() {};

  MorphoProtocolEvm.prototype.getVaultAddress = function getVaultAddress() {
    return DEFAULT_VAULT_ADDRESS;
  };
  MorphoProtocolEvm.prototype.getBorrowMarketId = function getBorrowMarketId() {
    return DEFAULT_MARKET_ID;
  };
  MorphoProtocolEvm.prototype.getSupplyRequirements = async function getSupplyRequirements() {
    state.protocolCalls.push({ name: "getSupplyRequirements" });
    if (state.approvalSatisfied) {
      return [];
    }
    return [
      {
        to: APPROVAL_SPENDER,
        value: 0n,
        data: APPROVAL_DATA,
        action: {
          type: "erc20Approval",
          args: {
            spender: APPROVAL_SPENDER,
            amount: BigInt(options.requiredAmount ?? 1_000_000n),
          },
        },
      },
    ];
  };
  MorphoProtocolEvm.prototype.quoteSupply = async function quoteSupply(params) {
    state.protocolCalls.push({ name: "quoteSupply", params });
    return { fee: BigInt(options.operationFee ?? 7n) };
  };
  MorphoProtocolEvm.prototype.supply = async function supply(params) {
    state.protocolCalls.push({ name: "supply", params });
    return this._evmAccount.sendTransaction({
      to: GENERAL_ADAPTER,
      value: 0n,
      data: MORPHO_OPERATION_DATA,
    });
  };
  MorphoProtocolEvm.prototype.getBorrowRequirements = async function getBorrowRequirements() {
    state.protocolCalls.push({ name: "getBorrowRequirements" });
    if (state.authorizationSatisfied) {
      return [];
    }
    return [
      {
        to: MORPHO_CORE,
        value: 0n,
        data: AUTHORIZATION_DATA,
        action: {
          type: "morphoAuthorization",
          args: {
            authorized: GENERAL_ADAPTER,
            isAuthorized: true,
          },
        },
      },
    ];
  };
  MorphoProtocolEvm.prototype.quoteBorrow = async function quoteBorrow(params) {
    state.protocolCalls.push({ name: "quoteBorrow", params });
    return { fee: BigInt(options.operationFee ?? 9n) };
  };
  MorphoProtocolEvm.prototype.borrow = async function borrow(params) {
    state.protocolCalls.push({ name: "borrow", params });
    return this._evmAccount.sendTransaction({
      to: GENERAL_ADAPTER,
      value: 0n,
      data: MORPHO_OPERATION_DATA,
    });
  };
  globalThis.fetch = async (url, request) => {
    if (String(url).startsWith("http://fake-rpc.local")) {
      return {
        ok: true,
        status: 200,
        async json() {
          const body = JSON.parse(String(request?.body || "{}"));
          if (body.method === "eth_getTransactionReceipt") {
            return {
              result: {
                status:
                  body.params?.[0] === state.operationHash
                    ? options.operationReceiptStatus || "0x1"
                    : "0x1",
                transactionHash: body.params?.[0] || `0x${"f".repeat(64)}`,
              },
            };
          }
          if (body.method === "eth_estimateGas") {
            return { result: "0x64" };
          }
          return { result: "0x1" };
        },
      };
    }
    throw new Error(`Unexpected fetch URL: ${String(url)}`);
  };

  return {
    state,
    restore() {
      WalletManagerEvm.prototype.getAccount = originals.getAccount;
      WalletManagerEvm.prototype.dispose = originals.disposeWallet;
      MorphoProtocolEvm.prototype.getVaultAddress = originals.getVaultAddress;
      MorphoProtocolEvm.prototype.getBorrowMarketId = originals.getBorrowMarketId;
      MorphoProtocolEvm.prototype.getSupplyRequirements = originals.getSupplyRequirements;
      MorphoProtocolEvm.prototype.quoteSupply = originals.quoteSupply;
      MorphoProtocolEvm.prototype.supply = originals.supply;
      MorphoProtocolEvm.prototype.getBorrowRequirements = originals.getBorrowRequirements;
      MorphoProtocolEvm.prototype.quoteBorrow = originals.quoteBorrow;
      MorphoProtocolEvm.prototype.borrow = originals.borrow;
      globalThis.fetch = originals.fetch;
    },
  };
}

test("morpho vault list returns discovery payload", async () => {
  const service = createService();
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    calls.push(body);
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          data: {
            v1: { items: [] },
            v2: {
              items: [
                {
                  address: DEFAULT_VAULT_ADDRESS,
                  symbol: "pyUSDm",
                  name: "Paypal USD Main",
                  listed: true,
                  asset: {
                    address: "0x0000000000000000000000000000000000000002",
                    symbol: "PYUSD",
                    decimals: 6,
                    name: "PayPal USD",
                    price: { usd: 1 },
                    yield: { apr: 0, lookback: 86400 },
                  },
                  chain: { id: 8453, network: "base" },
                  adapters: { items: [] },
                },
              ],
            },
          },
        };
      },
    };
  }, async () => {
    const result = await service.getMorphoVaults({ network: "base" });
    assert.equal(result.protocol, "morpho");
    assert.equal(result.vaultCount, 1);
    assert.equal(result.vaults[0].address, DEFAULT_VAULT_ADDRESS);
    assert.equal(result.vaults[0].version, "v2");
    assert.equal(result.vaults[0].asset.priceUsd, 1);
    assert.equal("price" in result.vaults[0].asset, false);
    assert.equal("adapters" in result.vaults[0], false);
    assert.equal(result.listedOnly, true);
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].operationName, "MorphoVaultList");
  assert.deepEqual(calls[0].variables.v1Where, { chainId_in: [8453], listed: true });
  assert.deepEqual(calls[0].variables.v2Where, { chainId_in: [8453], listed: true });
  assert.equal(calls[0].variables.v1OrderBy, "TotalAssetsUsd");
  assert.equal(calls[0].variables.v2OrderBy, "TotalAssetsUsd");
  assert.equal(calls[0].variables.orderDirection, "Desc");
  // Every APY average must name its window; a bare avgNetApy silently means 6 hours.
  assert.match(calls[0].query, /avgNetApy7d: avgNetApy\(lookback: SEVEN_DAYS\)/);
  assert.match(calls[0].query, /avgNetApy30d: avgNetApy\(lookback: THIRTY_DAYS\)/);
  assert.doesNotMatch(calls[0].query, /\bavgNetApy\s*\n/);
  assert.doesNotMatch(calls[0].query, /priceUsd/);
});

test("morpho vault list merges V1 and V2 vaults into one ordered list", async () => {
  const service = createService();
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    calls.push(JSON.parse(String(options?.body || "{}")));
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          data: {
            v1: {
              items: [
                {
                  address: "0x00000000000000000000000000000000000000a1",
                  name: "Legacy USDC",
                  listed: true,
                  asset: { address: "0xusdc", symbol: "USDC", decimals: 6, price: { usd: 1 } },
                  chain: { id: 8453, network: "base" },
                  liquidity: { underlying: "500", usd: 500 },
                  warnings: [],
                  state: {
                    totalAssets: "1000",
                    totalAssetsUsd: 1000,
                    fee: 0.1,
                    apy: 0.05,
                    netApy: 0.045,
                    netApyExcludingRewards: 0.04,
                    avgNetApy7d: 0.044,
                    avgNetApy7dExcludingRewards: 0.039,
                    avgNetApy30d: 0.042,
                    allRewards: [{ supplyApr: 0.005, asset: { address: "0xwell", symbol: "WELL" } }],
                    curators: [{ name: "Gauntlet" }],
                  },
                },
              ],
            },
            v2: {
              items: [
                {
                  address: "0x00000000000000000000000000000000000000b2",
                  name: "New USDC",
                  listed: true,
                  totalAssetsUsd: 3000,
                  liquidityUsd: 100,
                  netApy: 0.05,
                  avgNetApy7d: 0.049,
                  avgNetApy30d: 0.047,
                  performanceFee: 0.1,
                  managementFee: 0,
                  asset: { address: "0xusdc", symbol: "USDC", decimals: 6, price: { usd: 1 } },
                  chain: { id: 8453, network: "base" },
                  adapters: {
                    items: [
                      {
                        __typename: "MetaMorphoAdapter",
                        metaMorpho: { address: "0x00000000000000000000000000000000000000a1", name: "Legacy USDC" },
                      },
                    ],
                  },
                },
              ],
            },
          },
        };
      },
    };
  }, async () => {
    const result = await service.getMorphoVaults({ network: "base", orderBy: "LiquidityUsd", limit: 5 });
    assert.equal(result.vaultCount, 2);
    // V1 has no LiquidityUsd ordering server-side; the merged list is still sorted by it.
    assert.deepEqual(result.vaults.map((vault) => vault.version), ["v1", "v2"]);
    const [v1, v2] = result.vaults;
    assert.equal(v1.liquidityUsd, 500);
    assert.equal(v1.totalAssetsUsd, 1000);
    assert.equal(v1.performanceFee, 0.1);
    assert.equal(v1.managementFee, null);
    assert.equal(v1.netApy, 0.045);
    assert.equal(v1.avgNetApy7d, 0.044);
    assert.equal(v1.avgNetApy7dExcludingRewards, 0.039);
    assert.equal(v1.avgNetApy30d, 0.042);
    assert.equal(v1.rewards.length, 1);
    assert.deepEqual(v1.curators, ["Gauntlet"]);
    assert.equal("state" in v1, false);
    assert.equal(v2.avgNetApy7d, 0.049);
    assert.deepEqual(v2.underlyingVaults, [
      { address: "0x00000000000000000000000000000000000000a1", name: "Legacy USDC", version: "v1" },
    ]);
  });

  assert.equal(calls[0].variables.v1OrderBy, "TotalAssetsUsd");
  assert.equal(calls[0].variables.v2OrderBy, "LiquidityUsd");
});

test("morpho vault lookup by address finds a V1 vault", async () => {
  const service = createService();
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    calls.push(JSON.parse(String(options?.body || "{}")));
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          data: {
            v1: {
              items: [
                {
                  address: "0xbeeF010f9cb27031ad51e3333f9aF9C6B1228183",
                  name: "Steakhouse USDC",
                  asset: { address: "0xusdc", symbol: "USDC", decimals: 6, price: { usd: 1 } },
                  metadata: { description: "desc", image: null },
                  state: {
                    netApy: 0.035,
                    avgNetApy7d: 0.0349,
                    avgNetApy30d: 0.0333,
                    allocation: [
                      {
                        supplyAssetsUsd: 10,
                        market: {
                          marketId: DEFAULT_MARKET_ID,
                          loanAsset: { symbol: "USDC", address: "0xusdc" },
                          collateralAsset: { symbol: "cbBTC", address: "0xcbbtc" },
                        },
                      },
                    ],
                  },
                },
              ],
            },
            v2: { items: [] },
          },
        };
      },
    };
  }, async () => {
    const result = await service.getMorphoVaults({
      network: "base",
      vaultAddress: "0xbeeF010f9cb27031ad51e3333f9aF9C6B1228183",
    });
    assert.equal(result.found, true);
    assert.equal(result.vault.version, "v1");
    assert.equal(result.vault.avgNetApy7d, 0.0349);
    assert.equal(result.vault.allocation.length, 1);
    assert.equal(result.vault.allocation[0].market.marketId, DEFAULT_MARKET_ID);
    assert.equal(result.vault.metadata.description, "desc");
  });

  assert.equal(calls[0].operationName, "MorphoVaultByAddress");
  assert.equal(calls[0].variables.chainId, 8453);
});

test("morpho discovery supports Arc mainnet", async () => {
  const service = createService({ network: "arc" });
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    calls.push(body);
    return {
      ok: true,
      status: 200,
      async json() {
        return { data: { markets: { items: [] } } };
      },
    };
  }, async () => {
    const result = await service.getMorphoMarkets({ network: "arc" });
    assert.equal(result.network, "arc");
    assert.equal(result.chainId, 5042);
    assert.equal(result.protocol, "morpho");
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].operationName, "MorphoMarketList");
  assert.deepEqual(calls[0].variables.where, { chainId_in: [5042], listed: true });
});

test("morpho vault list applies asset filter and custom ordering", async () => {
  const service = createService();
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    calls.push(body);
    return {
      ok: true,
      status: 200,
      async json() {
        return { data: { v1: { items: [] }, v2: { items: [] } } };
      },
    };
  }, async () => {
    const result = await service.getMorphoVaults({
      network: "base",
      listedOnly: false,
      assetAddress: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
      orderBy: "apy",
      orderDirection: "asc",
    });
    assert.equal(result.orderBy, "Apy");
    assert.equal(result.orderDirection, "Asc");
    assert.deepEqual(result.assetAddressFilter, ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"]);
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].variables.v1Where, {
    chainId_in: [8453],
    assetAddress_in: ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"],
  });
  assert.deepEqual(calls[0].variables.v2Where, calls[0].variables.v1Where);
  assert.equal(calls[0].variables.v1OrderBy, "Apy");
  assert.equal(calls[0].variables.v2OrderBy, "Apy");
  assert.equal(calls[0].variables.orderDirection, "Asc");
});

test("morpho market list applies search, asset filters and ordering", async () => {
  const service = createService();
  const calls = [];

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    calls.push(body);
    return {
      ok: true,
      status: 200,
      async json() {
        return { data: { markets: { items: [] } } };
      },
    };
  }, async () => {
    const result = await service.getMorphoMarkets({
      network: "ethereum",
      search: "wstETH",
      loanAssetAddress: "0xdAC17F958D2ee523a2206206994597C13D831ec7",
      orderBy: "supplyapy",
    });
    assert.equal(result.orderBy, "SupplyApy");
    assert.equal(result.orderDirection, "Desc");
    assert.equal(result.search, "wstETH");
    assert.deepEqual(result.loanAssetFilter, ["0xdAC17F958D2ee523a2206206994597C13D831ec7"]);
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].operationName, "MorphoMarketList");
  assert.deepEqual(calls[0].variables.where, {
    chainId_in: [1],
    listed: true,
    search: "wstETH",
    loanAssetAddress_in: ["0xdAC17F958D2ee523a2206206994597C13D831ec7"],
  });
  assert.equal(calls[0].variables.orderBy, "SupplyApy");
  assert.equal(calls[0].variables.orderDirection, "Desc");
});

test("morpho market list rejects an invalid market id", async () => {
  const service = createService();
  await assert.rejects(
    () => service.getMorphoMarkets({ network: "ethereum", marketId: "not-a-hash" }),
    /marketId must be a 32-byte hex string/
  );
});

test("morpho market by id returns detailed payload", async () => {
  const service = createService();

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    assert.equal(body.operationName, "MorphoMarketById");
    assert.equal(body.variables.marketId, DEFAULT_MARKET_ID);
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          data: {
            marketById: {
              marketId: DEFAULT_MARKET_ID,
              lltv: "860000000000000000",
              loanAsset: { address: "0xloan", symbol: "USDC", decimals: 6, name: "USD Coin", price: { usd: 1 } },
              collateralAsset: {
                address: "0xcollateral",
                symbol: "cbBTC",
                decimals: 8,
                name: "Coinbase Wrapped BTC",
                price: { usd: 65000 },
              },
              supplyingVaultV2s: [
                { address: "0xvault1", name: "Steakhouse USDC", symbol: "steakUSDC" },
              ],
            },
          },
        };
      },
    };
  }, async () => {
    const result = await service.getMorphoMarkets({
      network: "base",
      marketId: DEFAULT_MARKET_ID,
    });
    assert.equal(result.protocol, "morpho");
    assert.equal(result.found, true);
    assert.equal(result.market.marketId, DEFAULT_MARKET_ID);
    assert.equal(result.market.supplyingVaultV2s.length, 1);
    assert.equal(result.market.loanAsset.priceUsd, 1);
    assert.equal(result.market.collateralAsset.priceUsd, 65000);
    assert.equal("price" in result.market.collateralAsset, false);
  });
});

test("morpho positions returns user overview from explicit address", async () => {
  const service = createService();

  await withMockedFetch(async (_url, options) => {
    const body = JSON.parse(String(options?.body || "{}"));
    assert.equal(body.operationName, "MorphoUserByAddress");
    assert.equal(body.variables.address, DEFAULT_ADDRESS);
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          data: {
            userByAddress: {
              address: DEFAULT_ADDRESS,
              marketPositions: [
                {
                  market: {
                    marketId: DEFAULT_MARKET_ID,
                    loanAsset: { address: "0xloan", symbol: "USDC", decimals: 6, name: "USD Coin" },
                    collateralAsset: {
                      address: "0xcollateral",
                      symbol: "cbBTC",
                      decimals: 8,
                      name: "Coinbase Wrapped BTC",
                    },
                  },
                  state: {
                    supplyShares: "10",
                    supplyAssets: "1000000",
                    supplyAssetsUsd: 1,
                    borrowShares: "0",
                    borrowAssets: "0",
                    borrowAssetsUsd: 0,
                    collateral: "0",
                    collateralUsd: 0,
                  },
                },
              ],
              vaultPositions: [
                {
                  vault: {
                    address: "0x00000000000000000000000000000000000000a1",
                    name: "Steakhouse USDC",
                    symbol: "steakUSDC",
                    asset: { address: "0xusdc", symbol: "USDC", decimals: 6, name: "USD Coin" },
                    state: { netApy: 0.035, avgNetApy7d: 0.0349, avgNetApy30d: 0.0333 },
                  },
                  state: { shares: "9", assets: "250000000", assetsUsd: 250 },
                },
                {
                  vault: {
                    address: "0x00000000000000000000000000000000000000d1",
                    name: "Seeded Vault",
                    symbol: "seed",
                    asset: { address: "0xusdc", symbol: "USDC", decimals: 6, name: "USD Coin" },
                    state: { netApy: 0.2 },
                  },
                  state: { shares: "1", assets: "1", assetsUsd: 0.000001 },
                },
              ],
              vaultV2Positions: [
                {
                  vault: {
                    address: DEFAULT_VAULT_ADDRESS,
                    name: "Paypal USD Main",
                    symbol: "pyUSDm",
                    asset: {
                      address: "0xasset",
                      symbol: "PYUSD",
                      decimals: 6,
                      name: "PayPal USD",
                    },
                    netApy: 0.05,
                    avgNetApy7d: 0.048,
                    avgNetApy30d: 0.046,
                  },
                  shares: "5",
                  assets: "5000000",
                  assetsUsd: 5,
                },
              ],
            },
          },
        };
      },
    };
  }, async () => {
    const result = await service.getMorphoPositions({
      network: "base",
      address: DEFAULT_ADDRESS,
    });
    assert.equal(result.protocol, "morpho");
    assert.equal(result.marketPositionCount, 1);
    assert.equal(result.vaultPositionCount, 2);
    assert.equal(result.hiddenDustVaultPositionCount, 1);
    assert.equal(result.address, DEFAULT_ADDRESS);
    const [v1, v2] = result.vaultPositions;
    assert.equal(v1.version, "v1");
    assert.equal(v1.assetsUsd, 250);
    assert.equal(v1.shares, "9");
    assert.equal(v1.vault.netApy, 0.035);
    assert.equal(v1.vault.avgNetApy7d, 0.0349);
    assert.equal(v2.version, "v2");
    assert.equal(v2.assets, "5000000");
    assert.equal(v2.vault.netApy, 0.05);
    assert.equal(v2.vault.avgNetApy7d, 0.048);
  });
});

test("morpho api graphql errors are shaped", async () => {
  const service = createService();

  await withMockedFetch(async () => ({
    ok: true,
    status: 200,
    async json() {
      return {
        errors: [{ message: "bad query" }],
      };
    },
  }), async () => {
    await assert.rejects(
      () => service.getMorphoVaults({ network: "base", orderBy: "apy" }),
      (error) => error?.errorCode === "morpho_api_failed" && /bad query/.test(error.message)
    );
  });
});

test("morpho vault supply quote reports approval requirements and send executes them", async () => {
  const service = createService();
  const harness = createMorphoHarness({
    requiredAmount: 2_500_000n,
    approvalFee: 3n,
    operationFee: 7n,
  });

  try {
    const quote = await service.quoteMorphoVaultOperation({
      seedPhrase:
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
      network: "base",
      vaultAddress: DEFAULT_VAULT_ADDRESS,
      token: DEFAULT_TOKEN,
      amount: "2500000",
      operation: "supply",
    });
    assert.equal(quote.protocol, "morpho");
    assert.equal(quote.surface, "vault");
    assert.equal(quote.requirements.required, true);
    assert.equal(quote.requirements.approvalRequired, true);
    assert.equal(quote.requirements.requirementCount, 1);
    assert.equal(quote.estimatedRequirementsFeeWei, "3");
    assert.equal(quote.estimatedOperationFeeWei, "10");

    const sent = await service.sendMorphoVaultOperation({
      seedPhrase:
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
      network: "base",
      vaultAddress: DEFAULT_VAULT_ADDRESS,
      token: DEFAULT_TOKEN,
      amount: "2500000",
      operation: "supply",
      expectedQuoteFingerprint: quote.quoteFingerprint,
    });
    assert.equal(sent.result.requirements.length, 1);
    assert.equal(sent.result.requirements[0].type, "approval");
    assert.equal(sent.result.requirementsFee, "3");
    assert.equal(sent.result.totalFee, "133");
    assert.equal(sent.result.gasEstimate, "100");
    assert.equal(sent.result.gasLimit, "130");
    assert.equal(sent.result.gasBufferBps, "13000");
    assert.equal(harness.state.sentTransactions.length, 2);
    assert.equal(harness.state.sentTransactions[0].data, APPROVAL_DATA);
    assert.equal(harness.state.sentTransactions[1].data, MORPHO_OPERATION_DATA);
    assert.equal(harness.state.sentTransactions[1].gasLimit, 130n);
    assert.ok(harness.state.protocolCalls.some((entry) => entry.name === "supply"));
  } finally {
    harness.restore();
  }
});

test("morpho market borrow quote reports authorization requirements and send executes them", async () => {
  const service = createService();
  const harness = createMorphoHarness({
    authorizationFee: 5n,
    operationFee: 9n,
  });

  try {
    const quote = await service.quoteMorphoMarketOperation({
      seedPhrase:
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
      network: "base",
      marketId: DEFAULT_MARKET_ID,
      token: DEFAULT_TOKEN,
      amount: "1000000",
      operation: "borrow",
    });
    assert.equal(quote.protocol, "morpho");
    assert.equal(quote.surface, "market");
    assert.equal(quote.requirements.required, true);
    assert.equal(quote.requirements.authorizationRequired, true);
    assert.equal(quote.requirements.sequence[0].authorized, GENERAL_ADAPTER.toLowerCase());

    const sent = await service.sendMorphoMarketOperation({
      seedPhrase:
        "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
      network: "base",
      marketId: DEFAULT_MARKET_ID,
      token: DEFAULT_TOKEN,
      amount: "1000000",
      operation: "borrow",
      expectedQuoteFingerprint: quote.quoteFingerprint,
    });
    assert.equal(sent.result.requirements.length, 1);
    assert.equal(sent.result.requirements[0].type, "authorization");
    assert.equal(sent.result.requirementsFee, "130");
    assert.equal(sent.result.totalFee, "260");
    assert.equal(harness.state.sentTransactions.length, 2);
    assert.equal(harness.state.sentTransactions[0].data, AUTHORIZATION_DATA);
    assert.equal(harness.state.sentTransactions[0].gasLimit, 130n);
    assert.equal(harness.state.sentTransactions[1].data, MORPHO_OPERATION_DATA);
    assert.equal(harness.state.sentTransactions[1].gasLimit, 130n);
    assert.ok(harness.state.protocolCalls.some((entry) => entry.name === "borrow"));
  } finally {
    harness.restore();
  }
});

test("morpho send waits for the final receipt and reports an onchain revert", async () => {
  const service = createService();
  const harness = createMorphoHarness({
    initialApprovalSatisfied: true,
    operationReceiptStatus: "0x0",
  });

  try {
    await assert.rejects(
      () =>
        service.sendMorphoVaultOperation({
          seedPhrase:
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
          network: "base",
          vaultAddress: DEFAULT_VAULT_ADDRESS,
          token: DEFAULT_TOKEN,
          amount: "1000",
          operation: "supply",
        }),
      (error) => error?.errorCode === "morpho_operation_reverted"
    );
    assert.equal(harness.state.sentTransactions.length, 1);
    assert.equal(harness.state.sentTransactions[0].gasLimit, 130n);
  } finally {
    harness.restore();
  }
});

// #sendMorphoProtocolOperation installs its own sendTransaction interceptor
// instead of going through #sendBufferedDefiTransactionWithSender, so its
// idempotency wiring has to be proven separately -- this is the exact
// operation the original double-withdraw incident was about.
test("morpho vault send flags a second identical operation as a likely duplicate, but still broadcasts it", async () => {
  const dataDir = tempDataDir();
  const service = createService({ dataDir });
  const harness = createMorphoHarness({ initialApprovalSatisfied: true });

  try {
    const params = {
      seedPhrase: SEED_PHRASE,
      network: "base",
      vaultAddress: DEFAULT_VAULT_ADDRESS,
      token: DEFAULT_TOKEN,
      amount: "5000000",
      operation: "supply",
    };

    const first = await service.sendMorphoVaultOperation(params);
    assert.equal(first.result.duplicate_warning, undefined, "first send must not be flagged");
    assert.ok(first.tx_hash, "first send must report a tx_hash");

    const second = await service.sendMorphoVaultOperation(params);
    assert.ok(
      second.result.duplicate_warning,
      "a second identical Morpho operation inside the window must carry duplicate_warning"
    );
    assert.equal(
      second.result.duplicate_warning.tx_hash,
      first.tx_hash,
      "duplicate_warning must point back at the first operation's tx_hash"
    );
    // Warn-not-block: the second operation must still have gone out for real.
    assert.equal(harness.state.sentTransactions.length, 2, "both operations must actually broadcast");
    assert.notEqual(second.tx_hash, first.tx_hash);
  } finally {
    harness.restore();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("a morpho operation that reverted onchain is not flagged as a duplicate on retry", async () => {
  const dataDir = tempDataDir();
  const service = createService({ dataDir });
  const params = {
    seedPhrase: SEED_PHRASE,
    network: "base",
    vaultAddress: DEFAULT_VAULT_ADDRESS,
    token: DEFAULT_TOKEN,
    amount: "5000000",
    operation: "supply",
  };

  const reverting = createMorphoHarness({
    initialApprovalSatisfied: true,
    operationReceiptStatus: "0x0",
  });
  try {
    await assert.rejects(
      () => service.sendMorphoVaultOperation(params),
      (error) => error?.errorCode === "morpho_operation_reverted"
    );
  } finally {
    reverting.restore();
  }

  const retry = createMorphoHarness({ initialApprovalSatisfied: true });
  try {
    const second = await service.sendMorphoVaultOperation(params);
    assert.equal(
      second.result.duplicate_warning,
      undefined,
      "retrying an operation that is known to have reverted must not warn about a duplicate"
    );
    assert.equal(second.confirmation_status, "confirmed");
  } finally {
    retry.restore();
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test("morpho send rejects a changed quote fingerprint", async () => {
  const service = createService();
  const harness = createMorphoHarness();

  try {
    await assert.rejects(
      () =>
        service.sendMorphoVaultOperation({
          seedPhrase:
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
          network: "base",
          vaultAddress: DEFAULT_VAULT_ADDRESS,
          token: DEFAULT_TOKEN,
          amount: "1000",
          operation: "supply",
          expectedQuoteFingerprint: "stale",
        }),
      (error) => error?.errorCode === "morpho_quote_changed"
    );
  } finally {
    harness.restore();
  }
});

// Vault lookups are cached per process, so each case uses its own vault address.
async function captureMorphoVaultEntityVersion(vaultAddress, vaultLookupResponse) {
  const service = createService();
  const harness = createMorphoHarness();
  const entityCalls = [];
  const originalGetMorphoClient = MorphoProtocolEvm.prototype._getMorphoClient;
  const originalGetChainId = MorphoProtocolEvm.prototype._getChainId;
  const harnessFetch = globalThis.fetch;
  MorphoProtocolEvm.prototype._getChainId = async function getChainId() {
    return 8453;
  };
  MorphoProtocolEvm.prototype._getMorphoClient = async function getMorphoClient() {
    const entity = (version) => ({
      async getData() {
        entityCalls.push(version);
        throw new Error("stop after entity selection");
      },
    });
    return {
      vaultV1: () => entity("v1"),
      vaultV2: () => entity("v2"),
    };
  };
  globalThis.fetch = async (url, request) => {
    if (String(url).startsWith("https://morpho-api.test")) {
      if (vaultLookupResponse instanceof Error) {
        throw vaultLookupResponse;
      }
      return { ok: true, status: 200, async json() { return vaultLookupResponse; } };
    }
    return harnessFetch(url, request);
  };
  try {
    await service
      .quoteMorphoVaultOperation({
        network: "base",
        address: DEFAULT_ADDRESS,
        operation: "withdraw",
        vaultAddress,
        tokenAddress: DEFAULT_TOKEN,
        amount: "1000000",
      })
      // The fake entity aborts right after it is chosen; only the selection matters here.
      .catch(() => {});
  } finally {
    MorphoProtocolEvm.prototype._getMorphoClient = originalGetMorphoClient;
    MorphoProtocolEvm.prototype._getChainId = originalGetChainId;
    harness.restore();
  }
  return entityCalls;
}

test("morpho vault operations build a V1 entity for a V1 (MetaMorpho) vault", async () => {
  const vaultAddress = "0x00000000000000000000000000000000000000c1";
  const calls = await captureMorphoVaultEntityVersion(vaultAddress, {
    data: { v1: { items: [{ address: vaultAddress, state: {} }] }, v2: { items: [] } },
  });
  assert.ok(calls.length > 0);
  assert.ok(calls.every((version) => version === "v1"));
});

test("morpho vault operations keep the V2 entity for a V2 vault", async () => {
  const vaultAddress = "0x00000000000000000000000000000000000000c2";
  const calls = await captureMorphoVaultEntityVersion(vaultAddress, {
    data: { v1: { items: [] }, v2: { items: [{ address: vaultAddress, adapters: { items: [] } }] } },
  });
  assert.ok(calls.length > 0);
  assert.ok(calls.every((version) => version === "v2"));
});

test("morpho vault operations fall back to the V2 entity when the Morpho API is down", async () => {
  const calls = await captureMorphoVaultEntityVersion(
    "0x00000000000000000000000000000000000000c3",
    new Error("ECONNREFUSED")
  );
  assert.ok(calls.length > 0);
  assert.ok(calls.every((version) => version === "v2"));
});
