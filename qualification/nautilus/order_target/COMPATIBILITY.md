# OP-B Nautilus order-capability target — 2026-10-28

## Decision

Pin **NautilusTrader 1.231.0**, Windows AMD64, vanilla **CPython 3.14.2 / cp314**, wheel `nautilus_trader-1.231.0-cp314-cp314-win_amd64.whl`. This is an isolated development/qualification candidate, not deployment authorization. No operational environment or application configuration was modified.

[PyPI release metadata](https://pypi.org/pypi/nautilus_trader/1.231.0/json) and the full package release index were queried directly. The highest non-yanked stable release was 1.231.0; manifest records the stable tail. [Official installation guidance](https://nautilustrader.io/docs/latest/getting_started/installation/) distinguishes stable 1.x from pre-release 2.x and warns of incompatible Python APIs. No requirement here justifies adopting an RC. Latest documentation is contextual evidence only, never proof of installed behavior.

Annotated tag `v1.231.0` object `d3e1685e979925d7b0ffacd1b3f442547686e18f` resolves through the official GitHub API to commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`. `manifest.json` pins source URLs, source file hashes, wheel/sdist published hashes, Python/ABI/platform, all 45 resolved artifacts, and the project requirements hash. Source tag association and two matching source files do not prove reproducibility of the entire binary build.

## Compatibility evidence

- PASS: pip 25.3 resolved the repository mandatory dependencies and Nautilus together, ignoring installed packages, wheels only. No optional Nautilus extras or project test extras were included.
- PASS: installed all 45 locked distributions into the new worktree-local `.native-probe-env` with `--require-hashes --only-binary=:all:`. `install-evidence.json` preserves installation artifact URLs/hashes/environment; `install.log` preserves installer output. The verbose pip report was reduced by removing package descriptions, with its original byte hash recorded.
- PASS: `pip check`: no broken requirements.
- PASS: exact installed versions, core Cython imports, Binance config and execution factory imports, and local OrderFactory LIMIT object with price 81490 and quantity 0.001. `runtime-probe.json` is actual output, not a fixture. The installed Binance execution source hash matches the commit-pinned source hash.
- PASS: five manifest guard tests, including hash/ABI mutation, prohibition of external mode activation and qualification inflation, and preservation of the legacy lock.

This validates dependency metadata consistency and a narrow runtime API slice; it does not validate every application API or all native dependencies. Direct project pins were unchanged. Native candidate requires Python >=3.12,<3.15 upstream; the project's broad >=3.11 declaration is not sufficient for this candidate. The exact lock is specific to Windows CPython 3.14 and must not be reused on other platforms or Python versions.

## Remaining gates

**TESTNET, DEMO, LIVE remain disabled.** No client, TradingNode, credential/account lookup, database, exchange request or model call was made. Native order qualification and ORD-07 native branch remain **NOT_RUN**. No MARKET/LIMIT/STOP/etc exchange capability is certified by constructing one local LIMIT object. Capability semantics, futures algo namespaces, callback limits, stocks, list/algo child ingestion, permit/recovery/idempotency remain implementation and integration work against this exact source.

Linux deployment remains **BLOCKED**: manifest includes a published manylinux CPython 3.14 x86_64 artifact for reference, but no Linux transitive lock, container digest or Linux runtime probe. Windows has no container digest by design. This Windows candidate must not clear the legacy Linux deployment gate. The original `qualification/nautilus/requirements.lock`, review_ref, compatibility map and inventory retain their existing meaning unchanged.

UV 0.11.19 compile attempts failed on TLS handshake EOF to files.pythonhosted.org, including system certificate retry. git ls-remote also failed transport. These were transport failures, not dependency incompatibility. Python urllib official API access and pip succeeded; no certificate verification was disabled.

## Reproduction (isolated, no operational environment)

From the worktree root, using Python 3.14.2 on Windows AMD64:

```powershell
python -m venv .native-probe-env
.native-probe-env/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: -r qualification/nautilus/order_target/requirements-win-cp314.lock
.native-probe-env/Scripts/python.exe -m pip check
.native-probe-env/Scripts/python.exe qualification/nautilus/order_target/probe.py
python tests/qualification/test_nautilus_order_target.py
```

All four final install/check/probe/test commands exited 0 in this run. Resolution command was `python -m pip install --dry-run --ignore-installed --only-binary=:all: --report <temporary-report> nautilus_trader==1.231.0 .`. No wheel binaries or virtual environment are committed. Source package/wheel hashes are exact in the manifest; an unchanged version alone is not sufficient provenance.
