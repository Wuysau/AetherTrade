# Preserved pre-interruption stock and protection evidence

These eleven losslessly compressed files were already untracked in the resumed
`paper-execution` checkout. Their originals are preserved unchanged, with raw
and compressed SHA-256 in manifest.json. This commit records existing evidence;
it does not rerun tests or establish final unified PAPER acceptance.

The three `*-evidence.txt.xz` reports give original source commits, exact commands,
exit codes, failures, successes and explicit limitations. They describe stock
payload generation, exact protection quantity/grid comparisons and owner-handoff
coverage precision. Corresponding integrated production changes were already
present at `2199e25`, `3083168` and `de56d45` when this chat resumed.

All three original batches used offline synthetic fixtures. PostgreSQL, venue,
native, Testnet and live qualification were NOT_RUN in those batches. Their
skipped cases remain NOT_RUN; newer actual PostgreSQL evidence is recorded
separately. In particular the historical >80-significant-digit risk/evaluator
limitation mentioned in the precision report is not retroactively marked fixed.

No original file or test counterexample was removed. The archived bytes were
checked against the local isolated test-cluster password strings before commit.
