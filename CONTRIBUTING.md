# Contributing to AetherTrade

Thanks for taking the time to contribute. This project is about making AI-assisted
trading safe enough to reason about, so review quality matters more than velocity.

## Ground rules

- **Paper only.** Do not enable, request, or document live trading, and do not test
  against a real account.
- **No secrets.** Never submit exchange credentials, account identifiers, order exports,
  private keys, seed phrases, production logs, or paid model tokens.
- **Fictional data.** Examples and fixtures use invented symbols, balances, and IDs.
- **Fail closed.** New code paths must have a defined behavior when evidence is missing,
  stale, or contradictory.
- **Honest evidence.** Say what you ran, what you did not run, and what it proves.
  A mocked scenario is never venue qualification.

## Before you start

- For small fixes, open a pull request directly.
- For anything touching order routing, risk limits, permissions, persistence, or
  recovery, open an issue first so the safety impact can be discussed.
- Keep pull requests focused. Large mixed changes are hard to review and easy to break.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"

python -m pytest -q
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web test
```

The suite is still being stabilized; if you fix a failure, mention the exact command in
the pull request.

## Pull requests

A good pull request includes:

1. What changed and why, including the user-visible or safety impact.
2. The commands you ran and their results.
3. What you deliberately did not test.
4. A note on whether any contract, schema, or migration changed.

Please keep the JSON Schemas in `contracts/` backwards compatible unless a contract
change is the point of the pull request.

## Style

- Python 3.11+, standard library first, type hints on public functions.
- Prefer pure functions and explicit state transitions over hidden mutation.
- Tests should assert observable behavior, not implementation details.
- No new runtime dependencies without a clear reason.

## Code of conduct

Be professional and discuss ideas in good faith. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
