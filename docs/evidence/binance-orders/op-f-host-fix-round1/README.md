# PAPER host IO fix — round1 component evidence

FixBASE19548ce410b82a3f146ab89d22fd69c884f5bfe2 (mergeddae), testedfix
1e8fd3748cea2b356d2ce94c55cdf27c95308716. Original independent report stays in
TradingHandoff/PaperHostReview-a799f19/review.md; implementation verdict pending
originalreviewer scoped re-review. Only observer/host/covering test changed.

`runs.json` preserves four original terminal handles, exactsource and fullJUnit:

- RED56183381 host24:7pass17fail,0error/skip158.834s, original45019.
- Fixed1e8 host24:0fail/error/skip202.688s, original54656.
- Fixed1e8 knownness8:0fail/error/skip45.952s, original68697.
- Fixed1e8 working17:0fail/error/skip110.422s, original43603.

Each used sharedPython with `-X utf8 tools/qualify_paper_docker.py --commit
EXACT_SOURCE --suite SUITE --timeout 1200 --runner-image
sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7
--offline-dependencies`. Cachedimages verified; oneactive qualifier only, no
build/pull/reset. Source resolves explicitly to the immutable manifest SHA.
OriginalNone-return7cases retained. Real V1/V2 funded commands first establish
Accepted/working in actual PG, then only the sourcequery is fault-injected with
OSError/TimeoutError/OperationalError. Actual ledger reads prove UNKNOWN committed
before IO; the same error propagates, all selectedbook/hold/token row+xmin stays,
everAccepted survives and real recovery works. Laterquery and barriercommit
negatives are included. No synthetic fill or caller-approved result substitutes.

24focusedunit passed4.33s for static source routing, projector, financial inputs
and original recovery. Console output is not represented as raw PG evidence.

copy-map binds64safe raw originals/copies; no env/sourcebundle is published.
Separate bundle-migration-map binds four new terminal sourcebundles45124403bytes
to approved privateC backups. Copy/size/SHA preceded single-file removal; backup
hashes rechecked. Old107map, evidence/env/DBs/main/archives/otherwriters untouched.
No livehandles/restarts remain; solequalifier slot explicitly returned to Cost.
No full74gate, effective qualification,46/56 acceptance or operational enablement.
