# Immutable contract metadata microbenchmark

Source9928147fe5f5822299a30619decb9ed39f4efe1d adds bounded code metadata caches:
dataclass type hints/field names keyed by actual Python type (256 entries), and
packaged JSON Schema validators keyed by kind/version (64 entries). Each payload
is still decoded and validated. No database state, authorization, current risk,
ALLOW, qualification, clock or lease is cached.

Two regression cases first failed because the same class hints and validator
were rebuilt3 times. They then passed along with406 contracts/admission/command/
target cases. Changing owner/epoch/money payloads still produces changed results;
bool-as-int and unknown fields still reject.

The direct comparison loads the real deterministic actual_archive fixture into a
ProgramFinancialBundle, and uses the decoder from git97b7710 versus the current
decoder. Both use the same current shared cached validator. Each of20 iterations
decodes the full bundle and checks exact canonical payload equality. The measured
work therefore includes decoding **and the equality check**, not isolated decoder
time. Result:0.5018272s versus0.2704536s,1.8555x. These are a local microbenchmark,
not a throughput SLA, PostgreSQL gate, proof of sufficient5s headroom or native
qualification. The packaged-validator optimization is also present but not
separately timed by this comparison.

comparison.json and profile.txt/profile.pstats are original tool-run artifacts.
The fixture fingerprint in comparison.json hashes sorted compact JSON using
Python's default ensure_ascii=True; it is a benchmark input fingerprint, not a
canonical financial proof or permit. copied-files-sha256.json records original
artifact bytes. A new fixed-source integrated PG/Python gate remains required.
