# ROOT native/quote source combination evidence

Final tested source9d8dd5c90ed1cade158bca0a825edac0890be4e9. `runs.json` records
ten original terminal jobs and JUnit results; `copy-map.json` binds160safe raw
originals/copies. Private container.env is not published. No job was restarted.

The first three2-case runs fail only fixture setup/admission (MARKET variant,
unbound model profile and actual full-loss limit). They are not productRED.
Source236af39 genuinely executes all three rivals before2 native source failures.
Source0657 fixes complete allocations but fails2 on a SQL join ambiguity; final
9d8 qualifies the join and passes both combined cases, including missing/repeated
quote rival denials, plus native27,quote30,V2source51,V2compat23.

All final suites have0fail/error/skip. Each invocation uses the shared Python,
exact immutable commit, fresh private PostgreSQL/no host ports and offline image:

```powershell
python -X utf8 tools/qualify_paper_docker.py --commit SOURCE --suite SUITE --timeout 1200 --runner-image sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7 --offline-dependencies
```

Separate focused local command (before the SQL-only manifest correction):

```powershell
python -X utf8 -m pytest -q tests/unit/test_program_release_preparation.py tests/unit/test_program_scoped_target.py tests/unit/test_paper_program_liquidity.py tests/unit/test_program_current_financial_inputs.py
```

Output53passed2.33s. This is not a raw PostgreSQL artifact or a replacement for
the actual post-correction suites. No static source/financial/host or full46/56
qualification is implied. Prior peer proofs retain their own source identities.
