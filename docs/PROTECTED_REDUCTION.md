# PAPER protected reduction

Selling a position with one healthy full-quantity synthetic stop now creates an
explicit durable protection adjustment receipt through the existing Store order
entry. `operation=PROTECTED_REDUCTION`, `phase=CANCEL_CONFIRM_REQUIRED` means
neither cancellation nor sale has completed. Quantity is the requested reduction.
The old stop remains until the synthetic platform confirms its cancellation.

For the explicit local CLI, use the same isolated PAPER config on each command:

1. `paper-reduce <stop-command-id> --quantity 0.5 --key <unique-key>` creates the
   audited CANCEL. The regular opening-only `paper-cancel` remains unchanged.
2. `paper-dispatch <cancel-id>` sends through the existing owner. Query the same
   ID with `paper-reconcile`; pending cancellation is UNKNOWN. Only an explicit
   `paper-event <cancel-id> --event-id <id> --status CANCELED --cumulative 0`
   confirms the synthetic cancellation. Events are controlled simulation input.
3. Use the separately restricted reviewer profile with `paper-recover <cancel-id>`
   to resolve proved original-command UNKNOWN. Protection GAP remains repairable
   under NO_NEW_RISK; an unresolved fault lock still blocks forbidden actions.
4. `paper-reduction-continue <cancel-id>` accepts the recorded bounded SELL using
   current console policy and fresh quote evidence. Dispatch its returned command
   ID, then explicitly inject fills. Repeating continuation returns the same ID.
5. For residual holdings use `paper-protect <close-id> --key <unique-key>`, dispatch
   and explicitly verify the replacement. A submit ACK does not verify protection.

CLI continuation uses saved quote evidence and rejects it after its maximum age;
it does not invent a fresh price. A console/API composition can supply current
public quotes to `continue_reduction(cancel_id, market, maximum_age, data)`.
Maintenance must honor `pending_protected_adjustment(tx)` until the reduction
has a terminal projection, preventing an early competing replacement stop.

Responsibility is continuous; stop coverage itself has a visible cancellation-to-
replacement gap. During that gap new risk remains locked. A race changing actual
quantity, ambiguous ownership, UNKNOWN, another working order or missing evidence
requires review instead of silently rebasing the authorized reduction. Full exit
needs no replacement. Partial adjustment under EXIT_ONLY is rejected.

The authenticated browser uses the normal SELL request to begin adjustment.
`execution.protection_adjustments` exposes its phase and original cancel ID.
Only `READY_TO_CONTINUE` shows the bounded continuation button. It posts to
`/api/paper/{cancel_command_id}/reduction-continue`, with the existing session,
Origin and CSRF checks; the server independently revalidates current quantities,
authorization and evidence. HTTP 202 is a PENDING command receipt, not a fill.
The same continuation ID is returned on replay. The browser cannot supply
reviewer credentials or bypass an unresolved UNKNOWN lock.

If the old stop fills while cancellation is pending, the original reduction
quantity becomes obsolete. After original-ID queries and separate UNKNOWN review,
maintenance proves the canceled stop terminal, all command outcomes known, no
working orders, current ownership, fresh complete evidence and the exact durable
fill balance. It records an immutable `INVALIDATED_QUANTITY_CHANGED` adjustment
audit and resumes protection maintenance for the actual residual quantity. It
creates no replacement sale and never clears risk locks. Old continuation returns
`REDUCTION_ADJUSTMENT_INVALIDATED`; before sufficient evidence the adjustment
remains pending with `REVIEW_REQUIRED`, rather than advertising readiness.
