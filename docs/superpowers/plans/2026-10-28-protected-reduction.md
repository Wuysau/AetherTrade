# Protected PAPER reduction

Scope: second P1 in HANDOFF-2026-10-28; dependency recovery commit 9f0b1cb
(local cherry-pick a707baa). No venue writes, implicit fills, or automatic risk
lock resolution. Existing user authorization permits completing this repair.

Root cause: intake and dispatch correctly count an existing stop as a working
SELL, while ordinary cancel intentionally only accepts an opening BUY. A new
SELL cannot safely coexist with a full-position spot stop without OCO semantics.

Design: existing Store SELL returns a durable protective adjustment CANCEL
receipt. An audit record binds that cancel to the verified full stop, owned
quantity and requested bounded reduction. Both dispatch checks independently
verify the exact current stop/owner/quantity and audit. No CANCEL exemption.
The operator explicitly confirms cancellation, reviews UNKNOWN with the separate
restricted reviewer, and continues the same bounded reduction. Durable fills
alone change holdings. Residual quantity remains a protection duty and requires
an explicit verified replacement. Intentional CANCELED stops are not failures;
REJECTED/EXPIRED stops and existing EXIT_ONLY/RECOVERY locks retain their policy.
Partial adjustment under EXIT_ONLY is refused because residual repair is barred.

Implementation sequence:
1. Capture real fresh-three-database failures against dependency tree.
2. Add risk/protection_adjustment.py for the narrow audit-bound proof.
3. Add runtime begin/continue adjustment, Store intake routing and maintenance
   pending guard; retain durable projection and independent command owner.
4. Add CLI explicit operations and describe cancel/review/continue/repair steps.
5. Run production provisioning regressions and existing locked-action suite.

The UI should explain the adjustment receipt phase and offer continuation via
authenticated API composition. The runtime does not fabricate cancellation,
fill or protection verification events. A racing stop fill invalidates the old
bounded continuation and requires review; unavailable proof fails closed.
