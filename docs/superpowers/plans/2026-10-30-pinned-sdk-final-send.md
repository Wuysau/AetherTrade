# Pinned SDK final-send guard implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Assigned worker implements Task 1; no subagents.

**Goal:** Close AetherTrade's implementation gap after the actual pinned Nautilus SDK queue, with an executable measured build and real loopback HTTP evidence, while all real venue activation remains disabled.

**Architecture:** Keep the existing owner/durable command/single-consume boundary and original permit window. Add a minimal reproducible extension to the exact SDK transport so an application-owned current guard executes after every SDK-owned rate-limit wait and immediately before HTTP dispatch. Bind it to the immutable original call and actual recheck_consumed; preserve ordinary quota enforcement. A host queue or an in-process replacement HTTP client is not proof of this boundary.

**Tech Stack:** Pinned Nautilus1.231.0 and source27a8e54e7ac3c57d6cbf8891f0283dfbaee97317; Rust/PyO3/Python; isolated build environment; local HTTP recorder; existing private PostgreSQL command qualifier.

**Spec:** ADR-ORDER-001/ADR-005; reference architecture and ORD-39/42/46/52/54; `docs/orders/OP-E-NATIVE-INTEGRATION.md`, `qualification/nautilus/order_target/manifest.json` and current sdk_pins/native_program_adapter source.

## Global Constraints

- Every application write retains independent durable command, current execution owner and original one-use permit. Last-send recheck mints or renews nothing. UNKNOWN remains query-original-ID only.
- Keep all external_modes/default activation disabled. PAPER/RESEARCH loopback only; never real Binance, TESTNET/LIVE, accounts, real credentials or paid product models.
- Never replace the actual SDK network/rate limiter with a fake client and claim post-queue qualification. Do not disable/decrease quotas, silently retry an uncertain write, follow an unguarded write redirect, or use a convenient newer SDK branch as installed-version proof.
- Preserve exact original SDK pin/Windows inspection and old schemas/hashes/commands/recovery. Any patched platform build is an explicit new artifact/revision with source/patch/toolchain/ABI/file/wheel digests, not a silent upgrade.
- No public push, main/archive edits, destructive cleanup, global replacement of the user's SDK/venv, fake fills, cash/ownership/protection grants or safety-test deletion/skipping.
- No open business transaction spans SDK queue or HTTP. Preserve initial private MAY/consume/current checks; recheck actual original data after SDK waits without constructing a fake TradeIntent or token.
- Use only this new worktree/build outputs. Measure available resources; bound compilation concurrency/resources and never stop/reset other Docker jobs or alter global toolchain defaults. Retain upstream license/source notices.

## Review Focus

- The real installed/built SDK's quota delay occurs before the final guard; a genuine expired permit after that delay yields zero recorded HTTP writes.
- Current owner/lease/fence/cause/source/cash changes during that same SDK wait are observed; an old permit label or queued callback cannot authorize a send.
- Actual wire business fields/route/method still match the immutable request and fixed call; authentication timestamp/signature are transport data, not a newly resized business request.
- Lost ACK/connection reset cannot lead to an automatic second write, protocol fallback, fresh ID or renewed permit; redirects and hidden SDK retries remain controlled.
- Passing loopback transport tests proves only that measured build/guard boundary. No ACK, fill, native protection, account or LIVE qualification follows.

## Task 1: Actual pinned SDK post-queue guard and controlled loopback adapter

**Files:** Add reproducible build/patch/probe tooling under `qualification/nautilus/guarded_target/`; versioned measured-build helper and closed final-guard adapter under `src/trading_system/engine/nautilus/`; narrowly extend the concrete offline probe registry in bridge only if necessary. Add meaningful SDK queue/transport/actual PG tests and `docs/orders/OP-E-SDK-FINAL-SEND.md`. No financial/matcher/management migration changes; this task reserves no migration number.

**Interfaces:** Reuse `ProgramCommandBoundary`/`ProgramWritePermit`/`ExecutionOwner` and `recheck_consumed(boundary,permit,lease)` with original protected marks/consumptions. Preserve existing OfflineNativeCreationProbe and PinnedSdkOfflineCreationProbe meanings. Any new concrete guarded SDK probe must be PAPER/RESEARCH and loopback only, with no runtime-supplied URL/key/callback. Define exact new functions and build descriptor in the report before implementing. Management must retain its own permit contract; never accept a creation permit to authorize management.

- [ ] **Step 1: Baseline and real unsupported RED.** Measure the installed SDK/platform and exact source; verify pinned client.rs hash ee606aeb333f639398dd97c060566b29e0f5b029b32024760da4a9d55420eb41. Current source client.rs awaits rate limits then send_request, while python/http.rs exposes no final hook. Demonstrate absence on the actual original SDK without a fake HTTP substitute. Check exact build requirements; Cargo workspace source27a8 specifies Rust1.97.1/edition2024 (workspace Rust package version differs from Python package version). Never assume latest docs equal this artifact.
- [ ] **Step 2: Minimal explicit SDK extension and build.** Obtain the pinned official source in isolated runtime storage, preserve licensing and add a tracked minimal patch/build script. Expose the current-guard hook at the real post-rate-limit dispatch boundary, preserving every quota and ensuring no later SDK queue/retry/redirect evades it. Produce actual executable Rust/PyO3 evidence and a compatible measured Python SDK artifact. A Rust-only or thin test module can be an intermediate milestone, never falsely called a full SDK build. Keep all original SDK/Windows manifests unchanged; new platform/patch uses explicit provenance.
- [ ] **Step 3: Real loopback TDD.** Use the actual SDK HTTP/rate-limit implementation against a loopback recorder with public fake HMAC fixture values. Warm a real quota to create a wait; prove successful positive control, original expiry during wait, current authority revocation during wait, callback failure and missing guard/build rejection. Record actual request counts and unchanged business fields; no fabricated fill responses or real account requests. Verify lost ACK/connection reset has one write and original-ID UNKNOWN duties. Stop only your own local recorder/process.
- [ ] **Step 4: Existing durable boundary.** Wire the closed PAPER-only guarded SDK adapter to the actual owner/one consume/original current guard. Prove real private PG permits and zero external writes after expiry/revocation during SDK wait. Keep no business TX open across IO and retain original MAY/consumed identities after uncertainty. Resolve Python event-loop/GIL/owner-lock thread boundaries explicitly; no deadlock or unawaited callback before send. Unsupported action/transport kinds fail closed.
- [ ] **Step 5: Reviewable measured handoff.** Commit patch/tooling/product code plus actual source/build/probe/PG logs, commands, exit codes and digests. Document artifact installation into an isolated environment, recovery/revert without old UNKNOWN resubmission, exact compatibility/feature set and remaining external qualification. All original jobs terminal; no retry/restart solely for observation timeout. Return focused BASE-to-HEAD review package. Parent arranges independent review; no external activation is authorized.

Primary public source checked2026-10-30: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/27a8e54e7ac3c57d6cbf8891f0283dfbaee97317/crates/network/src/http/client.rs ; corresponding `crates/network/src/python/http.rs` and Cargo.toml. Read these exact files rather than a moving branch. Only public source was fetched.
