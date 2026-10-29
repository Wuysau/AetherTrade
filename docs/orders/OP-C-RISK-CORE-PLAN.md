# Common risk calculation extraction

Approved scope: one shared deterministic policy core and typed, execution-neutral
source/terms/metrics. No generic command, permit, reservation or venue enablement.

1. [x] Capture full legacy RiskDecision golden records from unchanged9b28899, including
   normal ALLOW, policy REPLAN and diagnostic DENY cases across products.
2. [x] TDD strict legacy terms/source binding and complete metric requirements. A
   ProgramExposure object or absent net/group/loss/holding/protection/count facts
   cannot masquerade as a full risk assessment.
3. [x] Extract common numeric policy, account-mode/leverage and spendable checks into
   shared functions called by the existing evaluator at the same points. Keep
   legacy price/delta/fee/margin/stop interpretation and existing diagnostic order.
4. [x] Reproduce and contain80-digit financial rounding with explicit exact arithmetic.
   Ordinary representable decisions/hashes must remain identical; unsafe precision
   loss denies explicitly. Run existing unit and genuine isolated PG regressions.

Remaining generic admission requires authentic source readers, versioned intent,
risk and command persistence, complete risk scenario/loss/holding/protection facts
and the same owner/permit pipeline. This slice must not grant those capabilities.
