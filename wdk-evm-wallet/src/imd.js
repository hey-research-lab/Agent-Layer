// IMD (https://imd.fun) paid actions: the two signatures one payment needs.
//
// IMD charges 0.5 IMD per action through x402 v2 `exact` with Permit2, plus an
// IMD-specific EIP-712 QuoteApproval of that exact payment. Both typed-data
// shapes are pinned here and never taken from the caller: the caller only
// supplies the values, and each value is checked against the IMD allowlist
// before anything is signed.

export const IMD_CHAIN_ID = 1;
export const IMD_TOKEN_ADDRESS = "0xd34a99bc0f67ae1bbd63c660e6d0b0dd03e263b7";
export const IMD_PAY_TO_ALLOWLIST = Object.freeze(["0x4e0fa57bde726079356537e2f34d671e9f41adbc"]);
export const IMD_API_ORIGIN = "https://api.imd.fun";
export const IMD_ACTIONS = Object.freeze([
  "job.open",
  "job.continue",
  "launch.open",
  "workflow.open",
  "oracle.request",
  "schedule.create",
  "schedule.topup",
]);
// Hard ceiling for one signed payment (10 actions' worth, or a schedule's runs).
export const IMD_MAX_PER_PAYMENT_RAW = 5_000_000_000_000_000_000n;
// IMD quotes live 600s; allow a little clock skew on top.
export const IMD_MAX_WINDOW_SECONDS = 900;

export const PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
export const X402_EXACT_PERMIT2_PROXY_ADDRESS = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001";

export const PERMIT2_WITNESS_TYPES = Object.freeze({
  PermitWitnessTransferFrom: [
    { name: "permitted", type: "TokenPermissions" },
    { name: "spender", type: "address" },
    { name: "nonce", type: "uint256" },
    { name: "deadline", type: "uint256" },
    { name: "witness", type: "Witness" },
  ],
  TokenPermissions: [
    { name: "token", type: "address" },
    { name: "amount", type: "uint256" },
  ],
  Witness: [
    { name: "to", type: "address" },
    { name: "validAfter", type: "uint256" },
  ],
});

export const IMD_QUOTE_APPROVAL_TYPES = Object.freeze({
  QuoteApproval: [
    { name: "resource", type: "string" },
    { name: "requesterScopeHash", type: "bytes32" },
    { name: "quoteId", type: "string" },
    { name: "quoteHash", type: "bytes32" },
    { name: "paymentHash", type: "bytes32" },
    { name: "action", type: "string" },
    { name: "asset", type: "address" },
    { name: "amount", type: "uint256" },
    { name: "payTo", type: "address" },
    { name: "expiresAt", type: "uint256" },
  ],
});

function imdError(message) {
  const error = new Error(message);
  error.errorCode = "imd_signature_refused";
  return error;
}

function plainObject(value, fieldName) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw imdError(`${fieldName} must be an object.`);
  }
  return value;
}

function address(value, fieldName) {
  const text = String(value ?? "").trim();
  if (!/^0x[a-fA-F0-9]{40}$/.test(text)) {
    throw imdError(`${fieldName} must be a 20-byte hex address.`);
  }
  return text;
}

function sameAddress(left, right) {
  return String(left).toLowerCase() === String(right).toLowerCase();
}

function uint(value, fieldName) {
  const text = String(value ?? "").trim();
  if (!/^[0-9]+$/.test(text)) {
    throw imdError(`${fieldName} must be a base-10 integer string.`);
  }
  return BigInt(text);
}

function bytes32(value, fieldName) {
  const text = String(value ?? "").trim();
  if (!/^0x[a-fA-F0-9]{64}$/.test(text)) {
    throw imdError(`${fieldName} must be 0x + 32 bytes of hex.`);
  }
  return text.toLowerCase();
}

function boundedString(value, fieldName, maxLength) {
  if (typeof value !== "string" || !value.trim() || value.length > maxLength) {
    throw imdError(`${fieldName} must be a non-empty string of at most ${maxLength} characters.`);
  }
  return value;
}

function assertImdChain(runtimeConfig) {
  if (Number(runtimeConfig?.chainId) !== IMD_CHAIN_ID) {
    throw imdError("IMD payments are signed on Ethereum mainnet only (network=ethereum).");
  }
}

function assertAmount(amount, fieldName) {
  if (amount <= 0n) {
    throw imdError(`${fieldName} must be greater than zero.`);
  }
  if (amount > IMD_MAX_PER_PAYMENT_RAW) {
    throw imdError(`${fieldName} exceeds the 5 IMD per-payment ceiling.`);
  }
}

function assertPayTo(payTo, fieldName) {
  if (!IMD_PAY_TO_ALLOWLIST.some((allowed) => sameAddress(allowed, payTo))) {
    throw imdError(`${fieldName} is not a known IMD treasury address.`);
  }
}

function assertWindowEnd(value, fieldName, nowSeconds) {
  if (value <= BigInt(nowSeconds)) {
    throw imdError(`${fieldName} is already in the past.`);
  }
  if (value > BigInt(nowSeconds + IMD_MAX_WINDOW_SECONDS)) {
    throw imdError(`${fieldName} is more than ${IMD_MAX_WINDOW_SECONDS}s in the future.`);
  }
}

export function buildImdPermit2TypedData(permit, runtimeConfig, nowSeconds = Math.floor(Date.now() / 1000)) {
  assertImdChain(runtimeConfig);
  const source = plainObject(permit, "permit");
  const permitted = plainObject(source.permitted, "permit.permitted");
  const witness = plainObject(source.witness, "permit.witness");

  const token = address(permitted.token, "permit.permitted.token");
  if (!sameAddress(token, IMD_TOKEN_ADDRESS)) {
    throw imdError("permit.permitted.token must be the IMD token.");
  }
  const amount = uint(permitted.amount, "permit.permitted.amount");
  assertAmount(amount, "permit.permitted.amount");
  const spender = address(source.spender, "permit.spender");
  if (!sameAddress(spender, X402_EXACT_PERMIT2_PROXY_ADDRESS)) {
    throw imdError("permit.spender must be the x402 exact Permit2 proxy.");
  }
  const nonce = uint(source.nonce, "permit.nonce");
  const deadline = uint(source.deadline, "permit.deadline");
  assertWindowEnd(deadline, "permit.deadline", nowSeconds);
  const to = address(witness.to, "permit.witness.to");
  assertPayTo(to, "permit.witness.to");
  const validAfter = uint(witness.validAfter, "permit.witness.validAfter");
  if (validAfter >= deadline) {
    throw imdError("permit.witness.validAfter must be before permit.deadline.");
  }

  return {
    domain: { name: "Permit2", chainId: IMD_CHAIN_ID, verifyingContract: PERMIT2_ADDRESS },
    types: PERMIT2_WITNESS_TYPES,
    primaryType: "PermitWitnessTransferFrom",
    message: {
      permitted: { token, amount: amount.toString() },
      spender,
      nonce: nonce.toString(),
      deadline: deadline.toString(),
      witness: { to, validAfter: validAfter.toString() },
    },
  };
}

export function buildImdQuoteApprovalTypedData(
  quoteApproval,
  runtimeConfig,
  nowSeconds = Math.floor(Date.now() / 1000)
) {
  assertImdChain(runtimeConfig);
  const source = plainObject(quoteApproval, "quoteApproval");

  const resource = boundedString(source.resource, "quoteApproval.resource", 512);
  if (!resource.startsWith(`${IMD_API_ORIGIN}/`)) {
    throw imdError(`quoteApproval.resource must be an ${IMD_API_ORIGIN} URL.`);
  }
  const action = boundedString(source.action, "quoteApproval.action", 64);
  if (!IMD_ACTIONS.includes(action)) {
    throw imdError(`quoteApproval.action '${action}' is not an IMD paid action.`);
  }
  const asset = address(source.asset, "quoteApproval.asset");
  if (!sameAddress(asset, IMD_TOKEN_ADDRESS)) {
    throw imdError("quoteApproval.asset must be the IMD token.");
  }
  const amount = uint(source.amount, "quoteApproval.amount");
  assertAmount(amount, "quoteApproval.amount");
  const payTo = address(source.payTo, "quoteApproval.payTo");
  assertPayTo(payTo, "quoteApproval.payTo");
  const expiresAt = uint(source.expiresAt, "quoteApproval.expiresAt");
  assertWindowEnd(expiresAt, "quoteApproval.expiresAt", nowSeconds);

  return {
    domain: { name: "IdentityMD Paid Action", version: "1", chainId: IMD_CHAIN_ID },
    types: IMD_QUOTE_APPROVAL_TYPES,
    primaryType: "QuoteApproval",
    message: {
      resource,
      requesterScopeHash: bytes32(source.requesterScopeHash, "quoteApproval.requesterScopeHash"),
      quoteId: boundedString(source.quoteId, "quoteApproval.quoteId", 128),
      quoteHash: bytes32(source.quoteHash, "quoteApproval.quoteHash"),
      paymentHash: bytes32(source.paymentHash, "quoteApproval.paymentHash"),
      action,
      asset,
      amount: amount.toString(),
      payTo,
      expiresAt: expiresAt.toString(),
    },
  };
}

export function normalizeImdAllowanceAmount(value) {
  const amount = uint(value, "amount");
  assertAmount(amount, "amount");
  return amount;
}
