# AetherTrade

> A local, paper-only AI trading console — with deterministic risk controls and a single execution owner.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Node](https://img.shields.io/badge/node-22.12%2B-blue.svg)](web/package.json)
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
> real funds. The console trades a virtual PAPER account against read-only public
> market data. See [Project status](#project-status).

---

## Why

Most "AI trading bot" projects are a strategy script plus an exchange API key. That
works until the model hallucinates, the network times out, or a partial fill leaves the
process unsure whether an order exists. AetherTrade is built around those failure cases
instead of the happy path:

- **AI is a proposer, never an authority.** Model output is untrusted data that must
  pass schema, semantic, and risk validation before it can influence a position.
- **One execution owner per account and product.** Exactly one process may write orders,
  so duplicate or renegade paths are structurally impossible.
- **Persist before you send.** Commands are durable and idempotent; a crash after
  submission is recoverable, not a blind retry.
- **Risk is deterministic and enforced in code.** Budget reservations, daily-loss locks,
  and circuit breakers are checked outside the model's control.
- **Unknown is a state, not an exception.** Reconcilers classify unknown orders and
  refuse to resume trading until the account state is proven.

## Features

| | |
|---|---|
| **Local PAPER console** | Six responsive areas (overview, decisions, positions/orders, strategies, research, risk), authenticated local sessions, SQLite or PostgreSQL persistence |
| **Live market terminal** | Read-only Binance Spot quotes, real OHLC candles, five-level books and recent trades; public WebSocket with REST fallback and explicit stale/partial states |
| **AI model profiles** | Up to 20 named profiles for OpenAI Responses, OpenAI-compatible Chat and Claude Messages, with independent encrypted write-only keys |
| **Strategy automation** | Per-strategy model binding; single-model or opt-in 2–3-model cross-analysis; bell notifications and opt-in, risk-checked automatic PAPER orders |
| **Decision modes** | AI-driven, deterministic user rules, and explicit hybrid hand-off |
| **Single write path** | One execution owner per account/product ledger; AI never holds venue credentials |
| **Risk controls** | Per-strategy budget reservations, account-level limits, persistent loss locks, kill switches |
| **Durable execution** | Intent journal, idempotent order commands, reconciliation, recovery gates |
| **Protection lifecycle** | Hosted/native stop and exit management with disclosed fallbacks |
| **Markets (target)** | Binance Spot including bStocks tokenized stocks; USD-M perpetuals remain a separate future integration |
| **Contracts first** | JSON Schema for proposals, intents, commands, and risk policy |
| **Auditability** | Every decision, reservation, order, and recovery action is recorded |

## Quickstart

Requirements: Python 3.11+, Node.js 22.12+ (24 recommended). PostgreSQL mode also
requires a running Docker Desktop/Compose engine.

On Windows, from the repository root:

```powershell
.\scripts\start-console.ps1 -Database Postgres
```

The script creates an isolated environment, installs and builds the frontend, starts a
dedicated PostgreSQL 17 container on `127.0.0.1:55432`, initializes console tables and
opens **http://127.0.0.1:8787**. The first visit asks you to create a local user and an
initial virtual PAPER balance. No example positions or model decisions are inserted,
and public prices need no exchange key.

Prefer no Docker? Use the explicit local SQLite mode:

```powershell
.\scripts\start-console.ps1 -Database Local
.\scripts\stop-console.ps1                 # stop the API, keep data
.\scripts\stop-console.ps1 -StopDatabase   # also stop this checkout's PostgreSQL
```

Database modes keep separate accounts and data and are never silently interchanged.
`-Port`, `-DatabasePort`, `-NoBrowser`, `-NoMarket`, `-Foreground` and `-SkipInstall`
are available. Generated PostgreSQL credentials, logs and process records live under
the ignored `.runtime/` directory; stopping never deletes database volumes.

### Manual startup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
npm --prefix web ci
npm --prefix web run build

aethertrade serve                  # local SQLite on 127.0.0.1:8787
```

Set `AETHERTRADE_DATABASE_URL` to a `postgresql+psycopg://...` URL to use a supplied
PostgreSQL database. Optional settings are documented in `.env.runtime.example`;
copying it to `.env.runtime` is optional. The manual CLI does not load the generated
`.runtime/postgres.env`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AETHERTRADE_HOST` / `AETHERTRADE_PORT` | `127.0.0.1` / `8787` | Local API binding |
| `AETHERTRADE_DATABASE_URL` | local SQLite | Select PostgreSQL explicitly |
| `AETHERTRADE_MARKET_ENABLED` | `true` | Enable the read-only market terminal |
| `AETHERTRADE_MARKET_POLL_SECONDS` | `15` | Observation persistence cadence |
| `AETHERTRADE_QUOTE_MAX_AGE_SECONDS` | `90` | Staleness threshold for quotes |
| `AETHERTRADE_SESSION_HOURS` | `12` | Local session lifetime |
| `AETHERTRADE_AUTOMATION_ENABLED` | `true` | Allow opt-in strategy automation |

### AI and market sources

Model keys are stored encrypted and are write-only: the UI never returns a saved key.
Each AI strategy selects one profile, or explicitly opts into cross-analysis by 2–3
distinct profiles. Cross-analysis sends the same fixed snapshot to each model once,
with no extra arbitrator call, so a cycle may incur 2–3 provider charges; activation
requires acknowledging that cost. Only unanimous BUY or SELL may proceed through the
existing PAPER permission, ownership, quote and risk checks. Disagreement, HOLD, or a
failed participant means no automatic order. Agreement is not a correctness or
profitability claim, and manual analysis never places an order.

The market terminal defaults to Binance public REST/WebSocket data and can persist one
active custom source: a Binance-compatible service or a standard-JSON adapter, with an
optional write-only encrypted key. Saving, testing and restoring the official preset
are separate actions. A socket opening alone does not prove valid live data, and stale
or missing quotes cannot authorize a PAPER fill.

## Project status

AetherTrade is **early-stage and intentionally honest about it**. The runtime console is
usable for local PAPER workflows; the broader trading system is not qualified.

| Area | State |
|---|---|
| Web console (React/Vite, six areas, desktop + mobile) | Runnable, 98 tests passing, typecheck clean |
| Runtime API / CLI (`serve`, `init-db`, `doctor`) | Runnable, local sessions and persistence |
| Console persistence | PostgreSQL or explicit local SQLite |
| Live public markets | Binance Spot BTC/ETH/SOL plus TSLAB/NVDA/AAPL bStocks, WebSocket + REST fallback |
| PAPER operations | Manual spot buy/sell, fee/cash/risk checks, pause/resume, confirmed exit |
| AI model configuration | Up to 20 encrypted profiles; connection tests use controlled local providers |
| AI strategy automation | Single-model or 2–3-model cross-analysis, unanimous-only PAPER execution |
| Python suite | 2,355 tests collected; 20 known failures and 4 Windows-only environment errors |
| Rules / hybrid / native execution | Existing modules only; no native execution integration |
| Original PostgreSQL ledger migration | Reference schema, separate from console tables |
| NautilusTrader adapter | Offline bridge only — native evidence **NOT_RUN** |
| Binance Spot / USD-M / US Equities qualification | **NOT_RUN** |
| Live trading | **Disabled** |

Passing tests describe local behavior. They are not exchange qualification, not a
profitability claim, and not permission to trade real funds.

## Repository layout

```text
src/trading_system/         decision, rules, risk, execution, protection, recovery, research…
src/trading_system/server/  local API, sessions, persistence, market data, model profiles
web/                        React/Vite console, legacy view modules and tests
contracts/                  JSON Schemas and the control API contract
deploy/console.compose.yaml local PostgreSQL service
deploy/compose.yaml         original offline isolation template (not a product stack)
migrations/                 PostgreSQL ledger schema
qualification/              venue and engine capability records (all NOT_RUN)
tests/                      unit, integration, fault, security, contract and server tests
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

- [ ] Stabilize the full Python suite across Windows and Linux
- [x] Package `trading_system` and ship a local runtime CLI
- [x] Authenticated local PAPER console with persistent sessions and controls
- [x] Live market terminal with configurable public sources and REST fallback
- [x] Configurable model profiles and opt-in cross-analysis with controlled-provider tests
- [ ] Validate real provider integrations and native execution owners
- [ ] Automatic rule/hybrid scheduling
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
