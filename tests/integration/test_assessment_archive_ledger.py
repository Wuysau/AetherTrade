"""Real JSON/metadata persistence only; no financial admission or lock writes."""
from integration.support_ip_03 import isolated_postgres
from unit.test_assessment_archive import bounded_archive
from trading_system.intents.program_assessment_archive import (ProgramFinancialAssessmentArchiveCodec,
    financial_intent_from_assessment_archive,financial_risk_basis_from_assessment_archive)
from trading_system.risk.reduction import ReductionRiskMetrics
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


def test_actual_bounded_assessment_restarts_without_erasing_residual_duties(isolated_postgres):
    import psycopg
    archive=bounded_archive(missing_stop=True)
    codec=ProgramFinancialAssessmentArchiveCodec();digest=codec.hash(archive)
    intent=financial_intent_from_assessment_archive(archive)
    basis=financial_risk_basis_from_assessment_archive(intent,archive)
    scope=intent.scope
    with psycopg.connect(isolated_postgres) as db:
        db.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s)',(scope.environment.value,intent.collateral_group_id))
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',
            (scope.environment.value,scope.venue_account_id,scope.product.value,intent.collateral_group_id))
        db.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
            (scope.environment.value,scope.venue_account_id,scope.product.value,scope.instrument_uid,scope.venue_leg,
                intent.owner_strategy_id,intent.ownership_epoch,intent.risk_policy_revision))
    def connect_owner():
        db=psycopg.connect(isolated_postgres);db.execute('SET ROLE ledger_owner');db.commit();return db
    uow=UnitOfWork(connect_owner)
    with uow.transaction(scope) as tx:
        # Review metadata in the same existing ledger, not an IP-07 ALLOW or
        # financial bundle. Required lock/verifier/source duties remain pending.
        tx._append('audit_events','assessment-archive:'+intent.intent_id,archive,0)
        tx._append('audit_events','assessment-basis:'+intent.intent_id,basis,0)
        tx.intents.append(intent)
    with uow.transaction(scope) as tx:
        raw=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
            (scope_id(scope),'assessment-archive:'+intent.intent_id)).fetchone()[0]
        restored=codec.decode(raw)
        assert codec.hash(restored)==digest==tx.intents.get(intent.intent_id).hashes.archive
        assert type(restored.risk_metrics) is ReductionRiskMetrics and restored.risk_metrics.planned_loss is None
        assert restored.risk_metrics.protection_complete is False
        assert restored.risk_metrics.residual.hard_loss_bound is None
        assert {'PERSIST_RESIDUAL_RISK_LOCK','RETAIN_MANAGE_UNTIL_FLAT','CURRENT_LOCKED_ACTION_VERIFIER_REQUIRED'}<=set(restored.assessment.obligations)
        for table in ('commands','outbox','risk_locks','reservations'):
            assert tx.execute('SELECT count(*) FROM ledger.'+table+' WHERE scope_id=%s',(scope_id(scope),)).fetchone()[0]==0
    assert not intent.execution_authorized and not basis.execution_authorized
