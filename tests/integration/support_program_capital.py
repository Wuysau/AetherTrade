"""Explicit isolated-PG fixture provisioning; never used by application code."""
from pathlib import Path

_ready=set()


def ensure_current_capital_schema(dsn):
    if dsn in _ready:return
    import psycopg
    root=Path(__file__).resolve().parents[2]
    with psycopg.connect(dsn) as db:
        needs_boundary=db.execute("SELECT to_regclass('ledger.program_request_bindings')").fetchone()[0] is None
        if db.execute("SELECT to_regclass('ledger.order_closure_approvals')").fetchone()[0] is None:
            db.execute((root/'migrations/0009_order_program_closure.sql').read_text(encoding='utf-8'))
        if needs_boundary:
            db.execute((root/'migrations/0011_order_program_financial_credit_guard.sql').read_text(encoding='utf-8'))
            db.execute((root/'migrations/0012_program_command_boundary.sql').read_text(encoding='utf-8'))
        for table,name in (
            ('program_fill_source_approvals','0016_program_fill_source_approval.sql'),
            ('program_fill_source_snapshots','0017_program_fill_source_snapshots.sql'),
            ('program_fill_source_headers','0018_program_fill_source_leaves.sql'),
            ('program_account_openings','0019_program_account_opening.sql'),
            ('program_legacy_account_openings','0020_program_legacy_opening.sql')):
            if db.execute('SELECT to_regclass(%s)',('ledger.'+table,)).fetchone()[0] is None:
                db.execute((root/'migrations'/name).read_text(encoding='utf-8'))
    _ready.add(dsn)


def initialize_declared_assets(role_uow,scope,assets,at):
    from trading_system.contracts import types as t
    from trading_system.contracts.canonical import canonical_hash,domain_payload
    from trading_system.execution.program_account_opening import ProgramAccountOpeningService,account_key
    account=account_key(scope)
    # assets comes from the test's declared initial scenario, never a live book.
    assets=tuple(sorted(assets,key=lambda m:m.currency))
    evidence=t.EvidenceRef(id='declared-initial-assets:'+account.key,
        hash=canonical_hash(dict(account=domain_payload(account),assets=[domain_payload(m) for m in assets])),
        environment=t.Environment.PAPER,source='EXPLICIT_ISOLATED_INITIAL_ASSET_PROVISION',visible_at=at)
    return ProgramAccountOpeningService(role_uow('ledger_program_account_provisioner'),scope).initialize(assets,evidence=evidence)
