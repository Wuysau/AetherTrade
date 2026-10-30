# ROOT management integration evidence

Actual tested source2b94894f815b131c4e5f6be73f15ce5ce918f567,51passes,
0failures/errors/skips,260.764s. Original21158/run995fb20f8954 terminal0.
`copy-map.json` binds29safe raw originals/copies across two runs; `runs.json`
preserves exact source identities, outcome and setup-versus-test distinction.
Private container.env is not published.

Original9365/runa8ea2bfd0105 selected3654 before a test-and-commit shell completed.
Old3654 has no managementsuite, runnerexit2/0tests/noJUnit. It is retained as a
setup failure, not a productRED or qualification. No live job was restarted.

Actual qualifying command:

```powershell
python -X utf8 tools/qualify_paper_docker.py --commit 2b94894f815b131c4e5f6be73f15ce5ce918f567 --suite program-management --timeout 1200 --runner-image sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7 --offline-dependencies
```

Separate local focused command:

```powershell
python -X utf8 -m pytest -q tests/intents/test_order_management_compilation.py tests/intents/test_management_requests.py tests/unit/test_original_creation_recovery.py tests/unit/test_program_commands.py
```

Output89passed12.72s. One earlier bad-path invocation ran no tests and is no
verification claim. All original management test-function ASTs are unchanged;
the positive wrapper explicitly supplies protected opening/binding before first
financial admission, avoiding current shared fixture double initialization.
No risk guard, policy, migration, contract/history/hash or safety assertion was
weakened.12runtime/schema blobs exactly match independently reviewed peer source.

These proofs qualify the reviewed cancel-only component on ROOT's new26/quote
core. Neutral V4 hooks, protectedaction/amendment/replacement, financial bridges,
host/default qualification and full46/56 remain separate required work.
