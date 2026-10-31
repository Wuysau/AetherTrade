# Same-ledger financial registry: fixed-source full regression

Actual isolated command, Windows main venv controller, fresh task-only Linux/PG:

```text
python -X utf8 tools/qualify_paper_docker.py --commit 7d9deb7 --python-suite --timeout 5400
```

Launcher exit0. PostgreSQL915 passed in28 mandatory suites, zero failures,
errors or skips. Ordinary Python2974 passed,469 environment skips,96 deselected,
one existing Starlette/httpx deprecation warning. Ordinary skips are not
acceptance passes; mandatory PG suites reject any skip.

Source7d9deb7 includes shared exact risk/capacity, collateral proof preparation,
nonrenewable clock, source/target/financial records and strict ledger registry.
It proves existing safety plus new financial-record storage/role/restart/type
boundaries. It does not prove generic execution admission, full native lifecycle,
market matching, all46 features or all56 ORD scenarios. No real trading account,
native/Testnet/LIVE or paid product model call occurred.

The later archive TTL correction5f4e7eb has separate40 focused passes and agent
PG10; it is not part of this fixed-source full run. Further risk-assessment and
lifecycle changes need their own integrated verification.

Artifact bytes are unchanged; original/public paths and SHA256 are mapped in
artifact-map.json. Commands, source/migrations/roles, images and dependencies
are in the retained manifests/summary. Full original runtime evidence, including
synthetic final snapshots, remains under paper-qualification-12c067c04b95.
