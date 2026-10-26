# AetherTrade

> A safety-first, contract-driven framework for AI-assisted automated trading.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-experimental-orange.svg)](#project-status)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

AetherTrade explores a harder question than "can an LLM place an order?": **how do you
let AI participate in trading without giving it the keys, the risk budget, or the last
word?**

This repository is the open, paper-only implementation of that idea. AI proposes;
deterministic code decides what is allowed, reserves the budget, persists every intent,
and owns the single legal path to an exchange. Everything is auditable, recoverable,
and designed to fail closed.

> [!WARNING]
> **Research software. Not production ready. No live trading.**
> There is no certified venue integration, no qualification report, and no support for
> real funds. Run it with fictional data only. See [Project status](#project-status).

---

## Why

Most "AI trading bot" projects are a strategy script plus an exchange API key. That
works until the model hallucinates, the network times out, or a partial fill leaves the
process unsure whether an order exists. AetherTrade is built around those failure cases
instead of the happy path:

- **AI is a proposer, never an authority.** Model output is untrusted data that must
  pass schema, semantic, and risk validation before it can influence a position.
- **One execution owner per account and product.** There is exactly one process allowed
  to write orders, so duplicate or renegade paths are structurally impossible.
- **Persist before you send.** Commands are durable and idempotent; a crash after
  submission is recoverable, not a blind retry.
- **Risk is deterministic and enforced in code.** Budget reservations, daily-loss locks,
  and circuit breakers are checked outside the model's control.
- **Unknown is a state, not an exception.** Reconcilers classify unknown orders and
  refuse to resume trading until the account state is proven.

## Features

| | |
|---|---|
| **Decision modes** | AI-driven, deterministic user rules, and explicit hybrid hand-off between them |
| **Single write path** | One execution owner per account/product ledger; AI never holds venue credentials |
| **Risk controls** | Per-strategy budget reservations, account-level limits, persistent loss locks, kill switches |
| **Durable execution** | Intent journal, idempotent order commands, reconciliation, recovery gates |
| **Protection lifecycle** | Hosted/native stop and exit management with disclosed fallbacks |
| **Markets (target)** | Binance Spot, USD-M perpetuals, and real US equities/ETFs |
| **Research** | Paper simulation, point-in-time replay, experiment metrics, model cost budgets |
| **Control surfaces** | Mobile Web/PWA and CLI/TUI sharing one control API |
| **Contracts first** | JSON Schema for proposals, intents, commands, and risk policy |
| **Auditability** | Every decision, reservation, order, and recovery action is recorded |

## Project status

AetherTrade is **early-stage and intentionally honest about it**. The Python test suite
is partially green and still being stabilized; venue qualification has never been run.

| Area | State |
|---|---|
| Domain models, intent, risk, execution, protection modules | Implemented, unit/integration coverage |
| Contract schemas (`contracts/`) | Stable |
| Web control surface | Static views in progress |
| CLI/TUI | Prototype |
| PostgreSQL ledger migration | Reference schema |
| NautilusTrader adapter | Offline bridge only — native evidence **NOT_RUN** |
| Binance Spot / USD-M / US Equities qualification | **NOT_RUN** |
| Live trading | **Disabled** |

Passing unit tests describe local behavior. They are not exchange qualification, not a
profitability claim, and not permission to trade real funds.

## Quickstart

```bash
git clone https://github.com/Wuysau/AetherTrade.git
cd AetherTrade

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"

python -m pytest -q
```

The suite is a work in progress; some failures are currently expected. Windows users
need `tzdata` (installed with the test extra) for IANA timezone support.

Web workspace (TypeScript + Vitest, no runtime API yet):

```bash
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web test
```

There is no supported way to connect an exchange account, and you should not try.

## Repository layout

```text
src/trading_system/   decision, rules, risk, execution, protection, recovery, research…
contracts/            JSON Schemas and the control API contract
web/                  Web/PWA control surface
deploy/               Offline, sandboxed Compose template (no runtime binding)
migrations/           PostgreSQL ledger schema
qualification/        Venue and engine capability records (all NOT_RUN)
tests/                Unit, integration, fault, security, and contract tests
```

## Design rules

1. **Paper and simulation are the default.** Live requires deliberate, reviewed, and
   independently evidenced enablement that this repository does not ship.
2. **Fail closed.** Missing evidence, stale data, ambiguous order state, or a failed
   authorization means no new risk.
3. **No credentials in code, tests, or logs.** Secrets are references, never literals.
4. **The human owns the boundary.** AI may propose; policy, risk, and the execution
   owner decide.
5. **Never call a mock a qualification.** Only registered, independently reviewed
   evidence counts as proof of venue behavior.

## Roadmap

- [ ] Stabilize the full test suite across Windows and Linux
- [ ] Package `trading_system` and add a supported CLI entry point
- [ ] Complete the read-only control API and live web views
- [ ] Venue qualification: Binance Spot, USD-M, US equities
- [ ] Registered failover, recovery, and load evidence
- [ ] Threat model review before any live-mode proposal

## Contributing

Contributions are welcome — especially tests, fault injection, documentation, and
review of the risk and recovery paths.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) first.
- Never submit API keys, account data, order exports, or production logs.
- Keep examples fictional and paper-only.
- For anything touching order routing, risk limits, or permissions, open an issue before
  a large pull request.

## Security

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Bypassing the execution-owner boundary is treated as a funds-safety issue.

## License

Apache-2.0. See [LICENSE](LICENSE). Third-party dependencies keep their own licenses;
NautilusTrader (LGPL-3.0) obligations must be reviewed before any redistribution or
shipped integration.

---

**This project is software research, not financial advice.** Trading involves
substantial risk of loss, including losses beyond planned limits for leveraged products.
Do not point this software — or any AI model — at an account you cannot afford to lose.
