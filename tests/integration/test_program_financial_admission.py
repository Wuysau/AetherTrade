"""Real isolated PG SAME-kernel bundles; no venue, fills, permits or live reader."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from integration.support_ip_03 import isolated_postgres,ROOT
from unit.test_assessment_archive import actual_archive,archive_from_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.accounts.authorization import AuthorizationBinding,AuthorizationService
from trading_system.intents.generic_program_intake import GenericProgramIngress,ProgramIntakeInputs,intake_key
from trading_system.intents.ingress import IngressRevision,revision_key
from trading_system.intents.program_target_allocation import compile_program_target
from trading_system.risk.program_exposure import derive_program_exposure
from trading_system.risk.reservations import ReservationService
from trading_system.risk.program_financial_admission import (
    ProgramFinancialInputs,ProgramFinancialQualification,ProgramFinancialBundle,BUNDLE_PREFIX,
)
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


def digest(value):return canonical_hash(domain_payload(value))


@pytest.fixture(scope='module')
def database(isolated_postgres):
    import psycopg
    with psycopg.connect(isolated_postgres) as db:
        for migration in ('0006_proposal_intake_role.sql','0008_order_program_records.sql','0010_order_program_intake.sql'):
            db.execute((ROOT/'migrations'/migration).read_text(encoding='utf-8'))
    return isolated_postgres


def real_case(dsn,*,qualified=True):
    import psycopg
    from unit.support_program_financial import financial_case
    financial_case()
    import test_program_risk_assessment as risk_module
    import test_program_target_allocation as target_module
    original_inputs=risk_module.inputs;suffix=uuid4().hex
    def scoped_inputs(product):
        p,r,c,k=original_inputs(product);scope=replace(k['scope'],venue_account_id='isolated-'+suffix)
        qualification_ref=t.EvidenceRef(id='isolated-qualification-fixture',hash='c'*64,
            environment=scope.environment,source='SYNTHETIC_QUALIFICATION_FIXTURE_NOT_NATIVE',visible_at=r.as_of)
        r=replace(r,scope=scope);c=replace(c,scope=scope,qualification='PAPER_VERIFIED' if qualified else 'NOT_RUN',
            evidence=(qualification_ref,) if qualified else ())
        k={**k,'scope':scope,'profile':replace(k['profile'],scope=scope),
            'expected_capabilities_hash':VersionedOrderCodec().hash('order-capability-snapshot',c)}
        return p,r,c,k
    with patch.object(risk_module,'inputs',scoped_inputs),patch.object(target_module,'inputs',scoped_inputs):
        c=risk_module.risk_case()
    group='program-group-'+suffix
    tf=replace(c.facts.target_facts,collateral_group_id=group)
    budget=replace(c.facts.budget_facts,collateral_group_id=group)
    c.terms=replace(c.terms,collateral_group_id=group);c.policy=replace(c.policy,collateral_group_id=group)
    c.allocation=compile_program_target(c.plan,c.book,tf)
    c.exposure=derive_program_exposure(c.plan,budget,now=c.facts.inputs.clock.utc_now)
    c.facts=replace(c.facts,target_facts=tf,budget_facts=budget,
        inputs=replace(c.facts.inputs,collateral_group_id=group),
        protection=tuple(replace(p,allocation_hash=digest(c.allocation)) for p in c.facts.protection))
    context=tf.context;scope=c.book.scope;now=c.facts.inputs.clock.utc_now
    grant=t.Authorization(authorization_id=context.authorization_id,owner_strategy_id=c.terms.owner_strategy_id,
        environment=scope.environment,deployment_id=context.deployment_id,scopes=(scope,),
        maximum_capital=t.Money(currency='USDT',amount=D('1000000')),policy_revision=context.policy_revision,
        protection_options=(c.terms.protection_policy,),model_cost_budget=t.Money(currency='USDT',amount=D(0)),
        valid_from=now,expires_at=c.terms.valid_until,revoked_at=None)
    binding=AuthorizationBinding(authorization=grant,identity_id=context.identity_id,
        strategy_revision=context.strategy_revision,ownership_epoch=context.ownership_epoch,
        queue_environment=context.environment,capabilities=(),evidence=c.book.evidence)
    def role_uow(role):
        def connect():
            db=psycopg.connect(dsn);db.execute('SET ROLE '+role);db.commit();return db
        return UnitOfWork(connect)
    owner,intake=role_uow('ledger_owner'),role_uow('ledger_intake')
    with psycopg.connect(dsn) as db:
        db.execute('INSERT INTO ledger.collateral_mutex VALUES(%s,%s)',(scope.environment.value,group))
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',
            (scope.environment.value,scope.venue_account_id,scope.product.value,group))
        db.execute('INSERT INTO ledger.active_owners VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
            (scope.environment.value,scope.venue_account_id,scope.product.value,scope.instrument_uid,scope.venue_leg,
                c.terms.owner_strategy_id,c.terms.ownership_epoch,c.terms.policy_revision))
    fence=IngressRevision(sequence=1,identity_id=context.identity_id,deployment_id=context.deployment_id,
        authorization_id=context.authorization_id,owner_strategy_id=c.terms.owner_strategy_id,
        strategy_revision=context.strategy_revision,risk_policy_revision=context.policy_revision,
        ownership_epoch=context.ownership_epoch,enabled=True)
    with owner.transaction(scope) as tx:
        tx.projections.save_book(c.book,0)
        tx._append('audit_events',revision_key(context),fence,0)
    ingress_facts=ProgramIntakeInputs(compilation=c.plan,rules=c.facts.rules,capabilities=c.facts.capabilities,
        profile=c.facts.profile,authorization=binding,clock=c.facts.inputs.clock,
        expected_build_digest=c.plan.adapter_build_digest,expected_instrument_ref=c.plan.original_proposal.instrument_ref,
        allow_round_down=c.facts.inputs.allow_round_down)
    ingress=GenericProgramIngress(intake,scope,lambda *args:ingress_facts,clock=lambda:now)
    source,record=ingress.accept(c.plan.original_proposal,context)
    service=ReservationService(owner,scope,None,clock=lambda:now,monotonic_clock=lambda:c.facts.inputs.clock.monotonic_ns)
    capacity=(t.Money(currency='USDT',amount=D('1000000')),)
    def load(tx,actual_source,intake_record,book,clock):
        current=replace(c.facts.inputs,intent_id=intake_record.intent_id,clock=clock)
        tf=replace(c.facts.target_facts,transaction_id=tx.transaction_id,inputs=replace(
            c.facts.target_facts.inputs,clock=clock))
        c.allocation=compile_program_target(c.plan,book,tf)
        c.book=book;c.terms=replace(c.terms,decision_id=intake_record.intent_id)
        c.facts=replace(c.facts,inputs=current,target_facts=tf,
            authorization=AuthorizationService(lambda _:binding,clock=lambda:utc_timestamp(now)).validate(context),
            protection=tuple(replace(p,allocation_hash=digest(c.allocation),book_hash=digest(book)) for p in c.facts.protection))
        a=replace(archive_from_case(c),source=actual_source)
        return ProgramFinancialInputs(archive=a,authorization=binding,group_capacity=capacity,
            strategy_capacity=capacity,buffer=(t.Money(currency='USDT',amount=D(0)),))
    def qualify(tx,a,clock):
        return ProgramFinancialQualification(scope=scope,compilation_hash=digest(a.compilation),
            capabilities_hash=a.compilation.capabilities_hash,rules_hash=a.compilation.rules_hash,
            profile_hash=a.compilation.profile_hash,adapter_build_digest=a.compilation.adapter_build_digest,
            execution_mode=a.compilation.original_program.execution_mode,evidence=a.capabilities.evidence,
            valid_until=c.terms.valid_until)
    return SimpleNamespace(c=c,scope=scope,owner=owner,intake=intake,service=service,source=source,
        record=record,ingress=ingress,load=load,qualify=qualify,key=intake_key(c.plan.original_proposal,context),dsn=dsn)


def test_actual_shared_bundle_persists_and_restarts_in_the_existing_cash_ledger(database):
    c=real_case(database)
    bundle=c.service._transaction(lambda tx:c.service._reserve_program(tx,c.key,('original-request-id',),
        load_inputs=c.load,load_qualification=c.qualify,load_clock=lambda tx:c.c.facts.inputs.clock))
    assert bundle.intent.proposal.program.nodes[0].order.price.value=='81490.00'
    assert bundle.reservations[0].reservation.amount==D('81490')
    with c.owner.transaction(c.scope) as tx:
        stored=tx._latest('audit_events',BUNDLE_PREFIX+c.record.intent_id,ProgramFinancialBundle)
        assert digest(stored)==digest(bundle) and tx.intents.get(c.record.intent_id)==bundle.intent
        assert tx.reservations.for_intent(c.record.intent_id)[0].command_refs==('original-request-id',)
        assert tx.execute('SELECT count(*) FROM ledger.commands WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()[0]==0
    # This private financial component grants no send permit or success receipt.
    assert not bundle.execution_authorized


@pytest.mark.parametrize('fault',['missing_qualification','foreign_transaction','rollback_after_bundle','capacity',
    'missing_clock','unhealthy_clock','unqualified_capabilities','commit_failure','fence_changed',
    'expired_during_reader'])
def test_failed_admission_keeps_durable_intake_but_no_partial_financial_bundle(database,fault):
    c=real_case(database,qualified=fault!='unqualified_capabilities')
    if fault=='commit_failure':
        class FailBeforeCommit:
            def __init__(self,db):self.db=db
            def __getattr__(self,name):return getattr(self.db,name)
            def commit(self):raise OSError('injected real PostgreSQL commit failure')
        failing=UnitOfWork(lambda:FailBeforeCommit(c.owner.connection_factory()))
        c.service.uow=failing
    def load(*args):
        value=c.load(*args)
        if fault=='foreign_transaction':value=replace(value,archive=replace(value.archive,target_facts=replace(
            value.archive.target_facts,transaction_id='other-transaction')))
        if fault=='capacity':value=replace(value,group_capacity=(t.Money(currency='USDT',amount=D(1)),))
        if fault=='fence_changed':
            tx=args[0];context=value.archive.source.context
            previous=tx._latest('audit_events',revision_key(context),IngressRevision)
            tx._append('audit_events',revision_key(context),replace(previous,sequence=previous.sequence+1,
                enabled=False),previous.sequence)
        if fault=='expired_during_reader':c.service.clock=lambda:'2026-10-28T11:00:00Z'
        return value
    def operation(tx):
        result=c.service._reserve_program(tx,c.key,('original-request-id',),load_inputs=load,
            load_qualification=None if fault=='missing_qualification' else c.qualify,
            load_clock=None if fault=='missing_clock' else lambda tx:replace(c.c.facts.inputs.clock,
                clock_health='UNHEALTHY' if fault=='unhealthy_clock' else 'HEALTHY'))
        if fault=='rollback_after_bundle':raise OSError('injected attachment failure')
        return result
    with pytest.raises((ValueError,OSError)):c.service._transaction(operation)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert tx._latest('order_program_intakes',c.key,type(c.record))==c.record


def test_execution_owner_cannot_forge_a_bounded_generic_intake(database):
    import psycopg
    c=real_case(database)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with c.owner.transaction(c.scope) as tx:tx._append('order_program_intakes','forged',c.record,0)
