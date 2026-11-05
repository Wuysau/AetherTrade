# Binance Spot Demo operator controls: implementation checkpoint

This is an implementation checkpoint, not deployment, PostgreSQL, venue, fill, or trading acceptance. The supplied CLI remains fail-closed for binding and execution. Do not infer that an account can be connected for trading from the existence of the controls.

## Implemented in this slice

- With an explicitly injected runtime and verified session-bound Demo credentials, the authenticated controls expose bind, configuration of an existing AI model profile and bounded risk limits, explicit start, stop and reconciliation recovery. Configuration and start require separate acknowledgements. Start also binds the saved strategy revision and grant hash shown to the operator; unsaved edits block the UI start. Admission rechecks persisted bindings.
- An execution window tracks pending and running work at account scope. Stop/revocation requests cancellation but an in-flight worker is allowed to settle before the final durable pause and transport close. A failed pause remains a blocker. Stopping does not cancel venue orders, flatten positions or establish that exposure is protected. Model requests, including repair calls, recheck authority before network transmission.
- The authenticated observation endpoint derives scope from the current bound session, returns bounded durable decisions, commands, reports and validated fills, and distinguishes unknown send outcomes from venue acknowledgements and observed fills. It returns a potentially incomplete snapshot, not a full history. P&L is explicitly unavailable without adequate evidence. The UI presents these same limitations.
- Session credentials are ephemeral; connect/disconnect use session-scoped preflight generations, and the supported supplied launcher runs one worker. These in-process controls are not a cross-process fence. No client-supplied account scope or qualification-writing endpoint is exposed.

## Blocking gaps before real simulated trading

1. Implement and qualify durable cross-process stream ownership and credential rebind fencing on genuine, separately migrated PostgreSQL, including crash, session loss, stream draining, unknown sends, and original-ID recovery. Prevent stale-frame attribution and handle database connection/pooling affinity. No startup migration or provisioning is performed.
2. Establish a trustworthy measurement of the running immutable build, trusted account UID/epoch mapping, and evidence-bound qualification. The current configured SHA alone is not build attestation. There is no completed production path to create genuine venue qualification, so ordinary execution cannot bootstrap itself.
3. A separately authorized, tightly bounded qualification workflow must use real Demo orders, private-stream observations and reconciliation, preserve UNKNOWN and original IDs, and record only capabilities actually observed. ACK is not a fill; no fill must not be reported as fill acceptance. The account, order and cancellation permissions require separate user confirmation before any live simulated activity.
4. Reconciliation needs a verified account-history baseline, correct isolation of external trades and unknown orders, subscribed-stream/snapshot overlap and demonstrable gap detection. An existing account's history cannot safely be assumed to belong to this strategy.
5. A maximum 900-second window can end before the configured holding limit. Independent durable protection or verified safe cleanup/manual takeover is required for open orders and positions. A stopped state is not proof of a flat account.
6. A complete, correctly scoped and fee-aware realized/unrealized P&L view needs qualified baseline, complete fills and trustworthy prices. The current endpoint intentionally does not calculate or fabricate it.
7. Run genuine PostgreSQL concurrency, crash, migration compatibility and official Binance Spot Demo contract/private-stream qualification only with the required environment and explicit authorization. Offline SQLite and fakes are not evidence of any of these.

No venue connection, simulated order, paid model call, PostgreSQL provisioning, or Docker operation is part of this checkpoint. Independent review and the outstanding qualification work are required before representing the end-to-end trading chain as usable.
