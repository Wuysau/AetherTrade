# Actual model schema diagnosis and verification — 2026-10-28

The 17:00:39 normal scheduled DeepSeek call completed successfully at the provider
but was rejected at `schema_revision`: valid JSON, 1423 visible characters, no
code fence, all six expected outer fields present, version unequal to
`assessment_v1`. Output usage7481 is not visible character length. This proves
that increasing the output budget alone could not repair this response.
The diagnostic records only structural metadata, never hidden reasoning or raw
transport bodies. The prior actual wrong version string was not retained.

The original instructions named assessment_v1 without specifying its literal
field value, then appended the nested DecisionProposal schema with a separate
contract version. Instructions now explicitly bind outer and nested versions.
Only a complete envelope with a mismatched version is eligible for one bounded
repair using the existing call budget. Invalid output is never relabeled as valid.

Prompt revision2 then passed response parsing in both primary and repair calls,
but semantic validation rejected bare evidence field names / wrong price-watch
references. These were additional undocumented requirements in the prompt.
Prompt revision3 specifies full snapshot paths (market.quote.price,
market.features.5m.atr) and requires PRICE_ABOVE/PRICE_BELOW evidence_ref=quote,
NEW_BAR evidence_ref=candles:<interval>. Error explanations now carry these rules.
No validator, financial boundary or execution permission was weakened.

17:13:02 actual scheduled verification: DECIDED, WAIT_CONDITION, HOLD, NO_ACTION.
Primary plus one repair, two calls total, within existing max_calls=2. This is a
valid observation, not an order or a claim of predictive quality. All real calls
were normal user-enabled scheduling; this task issued no additional manual
inference request and did not inject order/fill/protection events.

Relevant backend regression: 124 passed, 1 PG-required test deselected. Frontend:
115 passed; typecheck/build succeeded. Explicit regression tests cover error
stage, redaction, exact schema instruction, evidence/watch rules, bounded repair
and repeated wrong-version rejection. Broader execution PG qualification was
not repeated for this prompt/parser diagnostic change.

API was reloaded during idle windows. User strategy revision8 and model
revision5 (no application token cap, 60s timeout) were preserved. New failures
now carry a specific parse-stage reason and human-readable component error;
historical records remain unchanged.
