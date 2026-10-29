# Explicit bounded EXIT risk metrics

Normal RiskMetrics retains its original fields, wire shape and completeness
requirements. Protected partial reductions now retain the nonzero planned risk
of their residual position and validate stop tick/direction/product support.
Only a flat desired position can use fees/funding-only residual loss.

An existing preauthorized emergency reduction may need to reduce a known
unprotected position. Blocking every such EXIT would defeat the existing
EXIT_ONLY policy; calling its residual protected would be false. This slice adds
explicit2026-10-29 BoundedReductionProof, ResidualRiskState and the tagged
ReductionRiskMetrics subtype. It does not add a proposal flag or permission.

The proof binds source/terms/allocation, current owned position/book/snapshot and
context, owner/authorization/policy, complete captured collateral-group facts,
existing EmergencyReduction limits, current known risk locks, complete report,
archive/duty references and expiry. Its scope is one actual owned signed position,
an ATOMIC strictly decreasing partial exit, no pending/contingent/new/reversal
quantity, no unresolved group commands/working consumers, and EXIT-capable known
locks. UNKNOWN/RECOVERY_REQUIRED never qualifies. Price/type/age/unit checks remain
active. Futures follows the existing one-way reduce-only boundary rather than
inventing a hedge exception. The same ExposureCalculator proves filled exposure
strictly decreases; no fill is generated or assumed to have happened.

Residual state records the exact nonzero signed quantity, UNPROTECTED/UNKNOWN
status, EXIT_ONLY permission, intended-stop estimate when known and an explicit
UNKNOWN hard-loss bound when it is not established. Unknown is never encoded as
zero loss or protection_complete=True. Normal completeness still rejects this
state; only the recognized versioned subtype represents this existing bounded
EXIT exception. Wrong tick/direction or unsupported specified native triggers
remain invalid; a genuinely absent stop under the bounded proof is unknown risk.

Mathematical ALLOW carries PERSIST_RESIDUAL_RISK_LOCK, RETAIN_MANAGE_UNTIL_FLAT and
CURRENT_LOCKED_ACTION_VERIFIER_REQUIRED. No lock, command or permit is written.
The outer financial/dispatch path must independently authenticate the archive,
re-read current locks/owner/quantity, apply the actual LockedActionVerifier and
persist the residual restriction/duty before any write. No production reader is
registered here; constructors/hashes/provider labels alone cannot authorize.

Financial archive/basis codecs must preserve the explicit subtype, unknowns and
obligations and reject any erasure into normal metrics. Existing archive kinds,
legacy schemas/hashes and historical records remain unchanged. Native qualifier,
TESTNET/LIVE, real accounts and model calls are not enabled.
