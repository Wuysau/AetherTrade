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
> real funds. The local console uses virtual PAPER funds and read-only public prices.
> See [Project status](#project-status).

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
| **Control surfaces** | Responsive Web console and runtime CLI; legacy CLI/TUI adapters retained separately |
| **Contracts first** | JSON Schema for proposals, intents, commands, and risk policy |
| **Auditability** | Every decision, reservation, order, and recovery action is recorded |

## Project status

AetherTrade is **early-stage and intentionally honest about it**. The Python test suite
is partially green and still being stabilized; venue qualification has never been run.

| Area | State |
|---|---|
| Domain models, intent, risk, execution, protection modules | Implemented, unit/integration coverage |
| Contract schemas (`contracts/`) | Stable |
| Web control surface | Runnable six-page React console, desktop/mobile, authenticated sessions |
| Runtime CLI | `aethertrade serve`, `init-db`, `doctor`; legacy terminal client also retained |
| Console persistence | PostgreSQL or explicit local SQLite; sessions, strategies, risk and PAPER activity |
| Public quotes | Binance market-data-only endpoint: BTCUSDT, ETHUSDT, SOLUSDT |
| PAPER operations | Manual spot buy/sell, fee/cash/risk checks, pause/resume and confirmed exit |
| AI / rule automatic execution | Configuration only; model/scheduler integration not enabled |
| Original PostgreSQL ledger migration | Reference schema, separate from console-owned tables |
| NautilusTrader adapter | Offline bridge only — native evidence **NOT_RUN** |
| Binance Spot / USD-M / US Equities qualification | **NOT_RUN** |
| Live trading | **Disabled** |

Passing unit tests describe local behavior. They are not exchange qualification, not a
profitability claim, and not permission to trade real funds.

## Quickstart

Requirements: Python 3.11+, Node.js 22.12+ (24 recommended). PostgreSQL mode also
requires a running Docker Desktop/Compose engine.

On Windows, from this checkout:

```powershell
.\scripts\start-console.ps1 -Database Postgres
```

This creates an isolated Python environment, installs/builds the frontend, provisions
a dedicated PostgreSQL 17 container on `127.0.0.1:55432`, initializes console-owned
tables and opens **http://127.0.0.1:8787**. First visit: create your local user and
choose the initial virtual account balance. No example positions or model decisions
are inserted. Public market quotes are read-only and require no exchange key.

Without Docker, choose the explicit local database mode:

```powershell
.\scripts\start-console.ps1 -Database Local
.\scripts\stop-console.ps1                 # stops API; data remains
.\scripts\stop-console.ps1 -StopDatabase   # also stops this checkout's PostgreSQL
```

Database modes have separate accounts/data; they are never silently interchanged.
Use `-Port`, `-DatabasePort`, `-NoBrowser`, `-NoMarket` or `-Foreground` as needed.
Subsequent runs may use `-SkipInstall`; the frontend is still rebuilt. Generated
PostgreSQL credentials live in ignored `.runtime/postgres.env`; logs and process
records also live under `.runtime/`. Stopping never deletes database volumes.

Manual startup / other platforms:

```bash
python -m venv .venv
source .venv/bin/activate  # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
npm --prefix web ci
npm --prefix web run build
aethertrade serve          # explicit default: local SQLite, loopback 8787
```

Set `AETHERTRADE_DATABASE_URL` to a `postgresql+psycopg://...` URL to select a supplied
PostgreSQL database. Optional settings are in `.env.runtime.example`; copying it
to `.env.runtime` is optional. The manual CLI does not load the generated
`.runtime/postgres.env` automatically. See [runtime operations](docs/RUNTIME.md)
for database selection, development proxy, data locations, tests and limitations.

The new console provides local PAPER operations; the original native execution,
AI/automatic rules, real account connectors and qualification gates remain separate.

## Repository layout

```text
src/trading_system/   decision, rules, risk, execution, protection, recovery, research…
src/trading_system/server/  runnable local API, sessions, persistence, public quotes
contracts/            JSON Schemas and the control API contract
web/                  React/Vite six-screen browser console and legacy view modules
deploy/console.compose.yaml  local PostgreSQL service
deploy/compose.yaml   original offline isolation template (not a runnable product stack)
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
- [x] Package `trading_system` and add a supported local runtime CLI entry point
- [x] Connect local PAPER web views, authenticated sessions and persistent controls
- [ ] Integrate native execution owners, model providers and automatic rule scheduling
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
