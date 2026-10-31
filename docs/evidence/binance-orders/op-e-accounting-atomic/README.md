# Existing-ledger atomic source/fill foundation

Fixed1faf83c real isolated PG RED:0pass/4fail/0error/skip; all missing prepare API.
Fixed7d5f4e9 real PG:4 new atomic tests passed and37 existing execution-evidence
regressions passed, each in a fresh isolated database/runner, no skipped tests.
New tests assert actual transaction_id AND physical xmin equality across source,
native fill, fees, domain event, notice and outbox; injected/caught failures roll
everything back; replay keeps a single native execution. A separate scope-before-
archive-IO unit first failed during extraction and now passes; both XMLs remain.

These IP12 synthetic normalized inputs prove persistence/validation/atomicity,
not platform matching, financial posting, account balance credit, protection or
the full46/56 acceptance. Later programme accounting must use the real V3
verifier and protected source approval. No old synthetic fill bypass certifies
the unfinished execution chain. Public EvidenceIngestor/FillLedger methods retain
commit-before-receipt and all original validation/correction/deduplication rules.

Raw runner/image/dependency/migration/log/XML files are exact copies with SHA256
in manifest.json. Private container.env files and credentials are not published.
