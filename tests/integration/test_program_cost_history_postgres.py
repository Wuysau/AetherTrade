"""Source-backed cost/history PostgreSQL qualification."""
from decimal import Decimal as D
from dataclasses import replace
from uuid import uuid4
import pytest
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.storage.models import scope_id,MissingEvidence
from integration.test_program_working_orders import projector
from trading_system.execution.program_cost_history import reconcile_program_cost_history,require_program_cost_history
from integration.test_program_v2_owned_posting import (isolated_postgres,command_database,
    source_database,cash_database,database,venue,venue_v2,setup)
from trading_system.ownership.registry import RegistryTx
from trading_system.execution.program_account_opening import read_program_account_opening


def test_fresh_native_cost_history(database,venue_v2,tmp_path):
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.cost_evidence.missing
            from trading_system.execution.program_cost_history import reconcile_program_cost_history,require_program_cost_history
            proof=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
            assert proof.remaining_cost_numerator==8157149 and proof.remaining_cost_denominator==250
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.4')
            assert not RegistryTx(tx).owner().position.cost_evidence.missing
        with c.service.uow.transaction(c.scope) as tx:
            assert require_program_cost_history(tx)==proof
            before=tx.projections.book(c.scope)
            assert reconcile_program_cost_history(tx,read_opening=read_program_account_opening)==proof
            assert tx.projections.book(c.scope)==before
    finally:codec.close()


def test_source_cost_plus_real_working_returns_none_without_new_writes(database,venue,tmp_path):
    from trading_system.execution.program_order_projection import read_program_working_inputs
    from trading_system.intents.program_target_allocation import compile_program_target
    from trading_system.intents.generic_program_intake import GenericProgramIngress
    from trading_system.intents.program_compilation import compile_order_program
    from trading_system.risk.program_quantity_scope import current_program_quantity_reservation_ids
    c,accounting,old_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'working')
    try:
        assert accounting.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        assert service.apply(c.write.command_id).status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
            before=tx.projections.book(c.scope)
        service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope);assert book==before
            assert book.positions[0].actual_quantity.value==D('.4') and book.active_orders[0].leaves_quantity.value==D('.6')
        proposal=replace(c.record.proposal,proposal_id='working-replan-'+uuid4().hex)
        original=c.ingress.load_inputs
        def intake_inputs(tx,p,context,book):
            facts=original(tx,p,context,book)
            plan=compile_order_program(p,facts.rules,facts.capabilities,profile=facts.profile,scope=c.scope,
                now=facts.clock.utc_now,expected_build_digest=facts.expected_build_digest,
                expected_rule_revision=facts.rules.revision,expected_capabilities_hash=c.c.plan.capabilities_hash,
                expected_instrument_ref=facts.expected_instrument_ref,allow_round_down=facts.allow_round_down)
            return replace(facts,compilation=plan)
        ingress=GenericProgramIngress(c.intake,c.scope,intake_inputs,clock=lambda:c.c.facts.inputs.clock.utc_now)
        source,intake=ingress.accept(proposal,c.record.context)
        assert intake.book_sequence==before.sequence and intake.basis_fingerprint==before.basis_fingerprint
        assert intake.intent_id!=c.bundle.intent.intent_id and not intake.execution_authorized
        with c.service.uow.transaction(c.scope) as tx:
            from dataclasses import fields
            from trading_system.intents.program_target_allocation import ProgramTargetFacts,ProgramScopedTargetFactsV2
            raw=tx.projections.book(c.scope);financial=c.service._prepare_collateral(tx,raw).book
            working=read_program_working_inputs(tx,raw)
            facts=intake_inputs(tx,proposal,intake.context,raw)
            old=c.c.facts.target_facts
            values={f.name:getattr(old,f.name) for f in fields(ProgramTargetFacts)}
            values.update(transaction_id=tx.transaction_id,compilation_hash=canonical_hash(domain_payload(facts.compilation)),
                book_sequence=financial.sequence,book_hash=canonical_hash(domain_payload(financial)),
                unit_binding=replace(old.unit_binding,compilation_hash=canonical_hash(domain_payload(facts.compilation))),
                inputs=replace(old.inputs,pending=working.pending,order_sides=working.order_sides))
            scoped=ProgramScopedTargetFactsV2(**values,contract_version='2026-10-30',record_kind='PROGRAM_SCOPED_TARGET_FACTS_V2',
                quantity_scope=c.scope,quantity_reservation_ids=current_program_quantity_reservation_ids(tx,raw,financial))
            # Authentic cost history now permits the core quantity calculation.
            assert raw.positions[0].actual_quantity.value+raw.active_orders[0].leaves_quantity.value==D(1)
            result=compile_program_target(facts.compilation,financial,scoped)
            assert result.delta.classification=='NONE' and result.delta.remaining.value==0
            assert tx.intents.get(intake.intent_id) is None and tx.reservations.for_intent(intake.intent_id)==()
            assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',
                (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.risk_locks.active(c.scope) and not book.positions[0].cost_evidence.missing
    finally:codec.close();old_codec.close()



def test_cost_revision_rollback_new_fill_and_immutable_quantity_proofs(database,venue_v2,tmp_path):
    from trading_system.engine.paper_market import PaperMatcher
    from integration.test_paper_market_matcher import event
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            before=tx.projections.book(c.scope)
            historical=tx.execute("SELECT payload,xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id LIKE 'program-owned-posting:%%'",
                (scope_id(c.scope),)).fetchall()
        with pytest.raises(RuntimeError,match='rollback'):
            with c.service.uow.transaction(c.scope) as tx:
                reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
                raise RuntimeError('rollback')
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope)==before
            first=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match='RECONCILIATION_REQUIRED'):require_program_cost_history(tx)
            second=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
            assert len(second.native_identities)==2 and first.native_identities[0] in second.native_identities
            assert RegistryTx(tx).owner().position.actual_quantity.value==D('.6')
            assert tx.execute('SELECT payload,xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
                (scope_id(c.scope),'program-owned-posting:'+first.native_identities[0])).fetchall()==historical
            assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
    finally:codec.close()


def test_owner_authored_complete_cost_pnl_and_book_cannot_replace_proof(database,venue_v2,tmp_path):
    from trading_system.execution.program_book_cash import bind_program_book_cash,require_current_program_book_cash
    from trading_system.contracts import types as t
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            registry=RegistryTx(tx);old=registry.owner();book=tx.projections.book(c.scope)
            forged=replace(old,sequence=old.sequence+1,position=replace(old.position,
                cost_evidence=replace(old.position.cost_evidence,query_succeeded=True,pagination_complete=True,missing=()),
                realized_pnl=t.Money(currency='USDT',amount=D(123))))
            registry.save_owner(old,forged)
            fake_book=replace(book,sequence=book.sequence+1,positions=(forged.position,))
            tx.projections.save_book(fake_book,book.sequence)
            bind_program_book_cash(tx,fake_book,read_opening=read_program_account_opening)
            with pytest.raises(MissingEvidence,match='COST_OWNER_HISTORY_INCOMPLETE'):
                require_current_program_book_cash(tx,fake_book,read_opening=read_program_account_opening)
    finally:codec.close()


def test_nonzero_initial_owner_is_never_zero_cost_genesis(database,venue_v2,tmp_path,monkeypatch):
    from trading_system.ownership.registry import OwnershipRegistry
    original=OwnershipRegistry.register
    def register(self,position,policy_revision,**kwargs):
        return original(self,replace(position,actual_quantity=replace(position.actual_quantity,value=D(1))),policy_revision,**kwargs)
    monkeypatch.setattr(OwnershipRegistry,'register',register)
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        # Posting itself rejects a nonzero owner against the protected empty book.
        assert service.apply(c.write.command_id).status=='RAW_RECORDED_ECONOMICS_BLOCKED'
        with pytest.raises(MissingEvidence):
            with c.service.uow.transaction(c.scope) as tx:
                reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==D(1)
    finally:codec.close()


def test_process_restart_and_same_account_peer_are_read_only(database,venue_v2,tmp_path):
    import os
    from integration.test_program_financial_admission import real_case
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        service.apply(c.write.command_id)
        peer=real_case(database,venue_account_id=c.scope.venue_account_id,
            collateral_group_id=c.c.terms.collateral_group_id,instrument_uid='cost-peer-'+uuid4().hex,
            provision_opening=False)
        def peer_rows():
            with peer.service.uow.transaction(peer.scope) as tx:
                return tuple((table,tx.execute('SELECT row_to_json(r)::text,xmin::text FROM ledger.'+table+
                    ' r WHERE scope_id=%s ORDER BY row_to_json(r)::text',(scope_id(peer.scope),)).fetchall()) for table in
                    ('book_snapshots','audit_events','positions','cashflows','risk_locks','domain_events','outbox'))
        before=peer_rows()
        with c.service.uow.transaction(c.scope) as tx:
            proof=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
            book=tx.projections.book(c.scope)
        assert peer_rows()==before
        read_end,write_end=os.pipe();child=os.fork()
        if child==0:
            os.close(read_end)
            try:
                with c.service.uow.transaction(c.scope) as tx:
                    assert require_program_cost_history(tx)==proof
                    assert reconcile_program_cost_history(tx,read_opening=read_program_account_opening)==proof
                    assert tx.projections.book(c.scope)==book
                with os.fdopen(write_end,'w') as stream:stream.write('verified')
            except BaseException:os._exit(71)
            os._exit(0)
        os.close(write_end)
        with os.fdopen(read_end) as stream:assert stream.read(32)=='verified'
        _,status=os.waitpid(child,0)
        assert os.waitstatus_to_exitcode(status)==0 and peer_rows()==before
    finally:codec.close()


@pytest.mark.parametrize('same_timestamp',(False,True))
def test_actual_native_oto_partial_sell_cost_and_exact_flat(database,venue_v2,tmp_path,same_timestamp):
    from datetime import timedelta
    from trading_system.contracts import types as t
    from trading_system.contracts.canonical import utc_timestamp
    from integration.test_paper_market_matcher_v2 import advanced_case
    from integration.test_paper_market_matcher import submit,event
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    from trading_system.ownership.registry import OwnershipRegistry
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    from trading_system.execution.program_owned_posting import ProgramOwnedSpotPosting
    from trading_system.execution.program_legacy_opening import read_program_local_account_opening
    from trading_system.execution.program_fill_source import ProgramFillSourceService
    from trading_system.execution.paper_program_v2_reports import PaperProgramV2ReportVerifier
    from trading_system.engine.nautilus.id_codec import VenueIdCodec
    from trading_system.engine.nautilus.native_events import NativeEventBridge
    from integration.test_program_fill_source_approval import source_uow
    c=advanced_case(database,venue_v2,tmp_path,template='OTO',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    p=t.ManagedPosition(position_id='owned:'+c.scope.instrument_uid,scope=c.scope,
        actual_quantity=t.Quantity(value=D(0),unit='BASE'),owner_strategy_id=c.c.terms.owner_strategy_id,
        owner_book_id='cost-oto-book',ownership_epoch=c.c.terms.ownership_epoch,cost_evidence=c.c.book.coverage,
        realized_pnl=None,unrealized_valuation=None,active_exit_plan_id=None,risk_locks=())
    OwnershipRegistry(c.service.uow).register(p,c.c.terms.policy_revision)
    codec=VenueIdCodec(c.scope)
    verifier=PaperProgramV2ReportVerifier(c.service.uow,c.scope,engine=c.engine,
        archive_bridge=NativeEventBridge(codec,tmp_path/'oto-source'))
    source=ProgramFillSourceService(source_uow(database),c.scope,verifier=verifier)
    service=ProgramPaperAccountingService(c.service.uow,c.scope,source_service=source,trade_id_scope='INSTRUMENT',
        financial_posting=ProgramOwnedSpotPosting(read_opening=read_program_local_account_opening))
    def reconcile():
        with c.service.uow.transaction(c.scope) as tx:
            return reconcile_program_cost_history(tx,read_opening=read_program_local_account_opening)
    try:
        submit(c);matcher=PaperMatcher(c.engine)
        matcher.apply_market_event(event(c,quantity='.4'))
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        reconcile()
        matcher.apply_market_event(event(c,sequence=2,quantity='.6',kind='DEPTH_DELTA'))
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        full=reconcile()
        assert full.remaining_cost_numerator==81490 and full.remaining_cost_denominator==1
        clock=c.c.facts.inputs.clock
        later=replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(seconds=0 if same_timestamp else 10)).isoformat(),
            monotonic_ns=clock.monotonic_ns+(0 if same_timestamp else 10**10))
        c.engine._clock=lambda:later
        matcher.apply_market_event(replace(event(c,sequence=3,ask='83001',quantity='1'),occurred_at=later.utc_now,
            bids=(DepthLevel(price='83000',quantity='.4'),)))
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        partial=reconcile()
        assert partial.remaining_cost_numerator==48894 and partial.remaining_cost_denominator==1
        assert partial.realized_numerator==604 and partial.realized_denominator==1
        matcher.apply_market_event(replace(event(c,sequence=4,ask='83001',quantity='1',kind='DEPTH_DELTA'),
            occurred_at=later.utc_now,bids=(DepthLevel(price='83000',quantity='.6'),)))
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        flat=reconcile()
        assert flat.remaining_cost_numerator==0 and flat.remaining_cost_denominator==1
        assert flat.realized_numerator==1510 and flat.realized_denominator==1
        with c.service.uow.transaction(c.scope) as tx:
            assert RegistryTx(tx).owner().position.actual_quantity.value==0
            assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',
                (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.reservations.for_intent(c.bundle.intent.intent_id) and tx.risk_locks.active(c.scope)
    finally:codec.close()


@pytest.mark.parametrize('earlier_nonzero',(False,True))
def test_protected_later_pretrade_book_requires_complete_zero_prefix(database,venue_v2,tmp_path,monkeypatch,earlier_nonzero):
    from trading_system.storage.uow import ProjectionRepository
    from trading_system.contracts import types as t
    import integration.test_program_financial_admission as admission
    original_case=admission.real_case;original_save=ProjectionRepository.save_book
    def scoped_case(*args,**kwargs):return original_case(*args,**{**kwargs,'scoped_quantities':True})
    def pretrade_snapshot(repo,book,expected):
        if expected!=0:return original_save(repo,book,expected)
        old=book
        if earlier_nonzero:
            prior=t.ManagedPosition(position_id='prior-position',scope=book.scope,
                actual_quantity=t.Quantity(value=D(1),unit='BASE'),owner_strategy_id='prior-owner',
                owner_book_id='prior-book',ownership_epoch=1,cost_evidence=book.coverage,
                realized_pnl=None,unrealized_valuation=None,active_exit_plan_id=None,risk_locks=())
            old=replace(book,positions=(prior,))
        original_save(repo,old,0)
        # A real append-only pretrade snapshot revision; the private consumption
        # later binds sequence 2. No private source or genesis marker is forged.
        return original_save(repo,replace(book,sequence=2),1)
    monkeypatch.setattr(admission,'real_case',scoped_case)
    monkeypatch.setattr(ProjectionRepository,'save_book',pretrade_snapshot)
    c,service,codec=setup(database,venue_v2,tmp_path,source_version=2)
    try:
        assert service.apply(c.write.command_id).status=='ASSET_POSITION_BOOK_POSTED_DUTIES_RETAINED'
        if earlier_nonzero:
            with pytest.raises(MissingEvidence,match='COST_ZERO_BOOK_HISTORY_REQUIRED'):
                with c.service.uow.transaction(c.scope) as tx:
                    reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
        else:
            with c.service.uow.transaction(c.scope) as tx:
                proof=reconcile_program_cost_history(tx,read_opening=read_program_account_opening)
                assert proof.remaining_cost_numerator==8157149
    finally:codec.close()
