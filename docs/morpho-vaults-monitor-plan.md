# Morpho vaults: integration hardening → proactive rebalance monitor

Status: in progress (branch `morpho-vaults-v1-apy`), started 2026-10-15.

## Agreed scope

- Protocol: Morpho vaults only — V1 (MetaMorpho) and V2.
- Chains: Base and Ethereum (Arc keeps working for existing tools but is not monitored).
- Migration cost (gas etc.) is computed and **shown**, never used to drop a candidate.
  No payback / break-even metric.
- Every move needs explicit user confirmation. No autonomous mode.
- Candidates are judged in a single scan on Morpho's realized 7d and 30d average
  net APY (no "two scans in a row" gating). Repeat notifications are deduplicated.
- Host-agnostic: the core lives in the wallet; scheduling is a thin per-host layer.

## Morpho API facts (verified live 2026-10-15)

- Endpoint `https://api.morpho.org/graphql`, public, no key. Config: `MORPHO_API_BASE_URL`.
- No rate-limit headers (CloudFront, `cache-control: no-store`).
- `avgNetApy` / `avgNetApyExcludingRewards` take `lookback:` (`ONE_HOUR`, `SIX_HOURS` *(default)*,
  `ONE_DAY`, `SEVEN_DAYS`, `THIRTY_DAYS`, `NINETY_DAYS`, `ONE_YEAR`, `INCEPTION`) on both
  `Vault.state` (V1) and `VaultV2`. Realized from share price, net of vault fees.
- `Asset.priceUsd` is deprecated, **removal 2026-10-21** → use `price { usd }`.
- V1 filters (`VaultFilters`) type addresses as `String`, V2 (`VaultV2sFilters`) as `Address`;
  V2 has no `assetSymbol_in`.
- `vaultByAddress` / `vaultV2ByAddress` return `NOT_FOUND`, which nulls the whole `data`
  object; use `vaults(where:{address_in})` + `vaultV2s(where:{address_in})` instead.
- `userByAddress(address, chainId)` returns both `vaultPositions` (V1) and `vaultV2Positions`;
  an unknown user gets empty lists, not an error.
- Positions include dust (1 wei dead-share deposits) → hide `assetsUsd < $1`.
- Same-named V1/V2 vaults (e.g. Gauntlet USDC Prime) are parallel vaults using
  `MorphoMarketV1Adapter`, not wrappers. V2 may still wrap a V1 via `MetaMorphoAdapter`.
- Writes: `@morpho-org/wdk-protocol-lending-morpho-evm@0.0.5` always builds `client.vaultV2(...)`;
  `@morpho-org/morpho-sdk` has `vaultV1(...)` with the same `getData/deposit/withdraw` API.

## Stage A — finish the current Morpho integration

| # | Task | Notes |
|---|------|-------|
| A0 ✅ | Replace `Asset.priceUsd` with `price { usd }` in all queries, flatten back to `priceUsd` in responses | Deadline 2026-10-21; ship as its own release |
| A1 ✅ | Vault data: spot `netApy` + `avgNetApy7d` + `avgNetApy30d` (+ 7d excluding rewards) | Drop the implicit 6h `avgNetApy` |
| A2 ✅ | V1 discovery: list merges V1+V2, one request; `version` on every vault; by-address finds either | Normalized vault shape shared by both versions |
| A3 ✅ | Positions: V1 + V2, each with vault version, spot rate and 7d average; hide dust | `get_evm_morpho_positions` |
| A4 ✅ | V1 writes: protocol subclass picks `vaultV1`/`vaultV2` by vault version | Verify with a live preview on a V1 vault |
| A5 ✅ | Tool descriptions (adapter + OpenClaw extension), `defi-yield-and-looping` references, CHANGELOG | |
| A6 ✅ | Tests: JS smoke (`test:morpho-runtime`), Python adapter smoke | |

## Stage B — rebalance monitor (after A ships)

1. ✅ `get_morpho_rebalance_report` (read-only): positions on Base+Ethereum → candidates with the
   same asset on the same chain → table with spot / 7d / 30d, reward share, TVL, exit liquidity,
   warnings, estimated migration gas. Hard filters: same asset+chain, listed, no red warnings,
   min TVL (configurable). Candidate must beat the current vault on both 7d and 30d.
   Young vaults (<30d history) flagged.
2. ✅ State under `OPENCLAW_HOME/defi_monitor/morpho_rebalance.json`: last scan per address and the
   moves it returned; candidates carry `isNew`. Failed networks keep their known moves.
3. ½ Due-check: `get_evm_morpho_positions` carries `rebalance_monitor.scan_due` (>3 days since the
   last scan). Native scheduling per host: pending.
4. Rebalance intent: withdraw → verify → deposit, one confirmation bound to stable params;
   gas re-quoted at preview time. A failed deposit leaves funds in the wallet and is reported.
