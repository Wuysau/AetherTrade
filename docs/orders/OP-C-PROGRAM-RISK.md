# Actual generic risk derivation — mathematical result only

RiskEvaluator.evaluate_program consumes exact original program, target allocation,
budget exposure, current book/policy and ProgramRiskFacts. It recomputes source
compilation, target and budget using captured current rules/profile/capabilities,
and checks original responsibility and effective nonrenewable data windows.
RiskFacts retain current RiskInputs, market and authorization plus every node's
price, fee, funding, margin, horizon and child-count bound. Their hash is the
entire fact record, not the old budget-only hash.

The existing ExposureCalculator handles current/working/unsent/proposed quantities
and no-fill/fill/contingent scenarios. Portfolio net comes from a real worst-net
scenario; group gross bounds conservatively cover the scenarios and still sum
exactly. Cumulative buy-plus-sell turnover is not used as portfolio net. BUY
principal stays its declared limit even when ask is higher. Future scenario marks
and physical remaining inventory are separate from funding costs and sale P&L.

Loss includes retained/new position stop/slippage bounds, entry/exit fees and
horizon funding. Captured existing_planned_loss/capital_in_use remain trusted
complete prior-obligation bounds as in the existing risk core. Derivative margin
must cover its executable ceiling at authorized leverage. Shared binding,
account-mode, protection-policy, portfolio/group/count, capital and spendable
predicates remain the same core used by legacy evaluation.

Retained-position risk includes protected partial reductions: quantity remaining
after the proposed reduction is not zero-risk. All nonflat specified stops use
exact tick checks and explicit unified-to-native product support. Spot native
supports unified LAST; futures MARK is explicitly MARK_PRICE and LAST is
CONTRACT_PRICE. BID_ASK is never silently converted to native LAST. Flat targets
need no fabricated new protective stop. Bounded unprotected emergency exits use
the separate versioned contract in OP-C-BOUNDED-REDUCTION-RISK.md, retaining an
explicit residual restriction rather than weakening normal completeness.

Protection is derived from exact owner/book/allocation/quantity/phase-specific
duty references and explicit live-order bounds. A bool cannot supply coverage;
native full-fill-only exits cannot cover entry partial fills. Other working
orders need not fill before this working leg activates exits, and software OCO
cannot borrow their inventory or silently reverse an owned leg. Receipt exits
retain actual-net/fee/lot activation duties; no child quantity is fabricated.

These are assumptions backed by the trusted reader's full captured facts; types,
labels and hashes alone do not authenticate a venue or duty. No production fact
reader, financial admission, command, permit or native qualification is installed.
Even mathematical ALLOW has execution_authorized=False. Actual and contingent
write admission must recheck owner/risk/price rules at execution. Existing OCO,
quote-order-quantity and residual blockers remain explicit; all46 goals remain.

This is in active verification. Source-integrated PostgreSQL and broader negative
tests will be reported in the evidence handoff; these unit fixtures do not count
as full ORD or venue acceptance. TESTNET/LIVE and account/model calls stay off.
