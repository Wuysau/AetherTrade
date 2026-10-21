# Public Repository Publication Policy

## Scope

- Repository is public and intended to accept outside review and contributions.
- Source code, non-sensitive test fixtures and approved design documents may be published.
- Real account secrets, signing keys, wallet seed phrases, order exports, account identifiers, production logs, private user data, and unpublished reports MUST NOT be committed.
- Study/reference code from NOFX, Condor, Hummingbot and NautilusTrader must respect their respective licenses; prefer linking to upstream and integrating through dependencies rather than copying.

## License

Own original contributions use Apache-2.0. Third-party notices, SBOM and notices for any vendored dependencies are maintained separately. NautilusTrader's LGPL-3.0 obligations must be reviewed if redistributed/linked in the shipped application. This is a documentation reminder, not legal advice.

## Branches and checks

- `main`: stable public project history; changes enter through reviewed PRs and green CI.
- `dev`: integration baseline; accepted task branches flow here after independent review.
- `task/ip-XX`: one task per worktree; one writer per worktree.
- Branch protections and status checks are configured in IP-37 when jobs exist. For solo developers, do not require approval that only the author could give themself.
- CI never uses live trading credentials, never calls production order-write APIs, and must not use a privileged untrusted-PR workflow.

## Safety gates

A `README` claiming support for an exchange or strategy must distinguish architecture intention from verified implementation. A test report must identify whether it ran with fake data, paper simulation, venue test, or authorized real account.
