"""Actual source-backed leaves; owned+working no-action is not another send."""
from dataclasses import replace
from decimal import Decimal as D
from uuid import uuid4

import pytest

from integration.test_program_owned_posting import isolated_postgres,command_database,source_database,cash_database,database,venue,setup
from integration.test_paper_market_matcher import case,submit
from integration.test_program_fill_source_approval import actual_verifier,source_uow
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.execution.program_submission_observation import ProgramSubmissionObservationService
from trading_system.storage.models import MissingEvidence,scope_id


def projector(c,database,path):
    from trading_system.execution.program_order_projection import ProgramPaperOrderProjectionService
    from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
    if type(c.engine._matching_model) is PaperMatchingModelV2:
        from trading_system.execution.paper_program_v2_reports import PaperProgramV2ReportVerifier
        from trading_system.engine.nautilus.id_codec import VenueIdCodec
        from trading_system.engine.nautilus.native_events import NativeEventBridge
        codec=VenueIdCodec(c.scope)
        verifier=PaperProgramV2ReportVerifier(c.service.uow,c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path))
    else:verifier,codec=actual_verifier(c,path)
    observer=ProgramSubmissionObservationService(source_uow(database),c.scope,verifier=verifier)
    return ProgramPaperOrderProjectionService(c.service.uow,c.scope,observer=observer),codec


def test_actual_accepted_unfilled_order_is_working_quantity_without_inventory_or_fill(database,venue,tmp_path):
    from trading_system.execution.program_order_projection import read_program_working_inputs
    c=case(database,venue,tmp_path,scoped_quantities=True);submit(c)
    service,codec=projector(c,database,tmp_path/'unfilled')
    try:
        result=service.apply(c.write.command_id)
        assert result.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED',result.reasons
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope);working=read_program_working_inputs(tx,book)
            assert book.positions==() and book.active_orders[0].leaves_quantity==t.Quantity(unit='BASE',value=D(1))
            assert working.order_sides[0].side=='BUY' and working.pending[0].signed_quantity.value==0
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.positions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            assert tx.reservations.for_intent(c.bundle.intent.intent_id)==tuple(p.reservation for p in c.bundle.reservations)
    finally:codec.close()


@pytest.fixture(scope='module')
def venue_v2(database,venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    from trading_system.engine.paper_market_v2 import provision_paper_market_v2
    provision_paper_market_v2(make_conninfo(**{**conninfo_to_dict(database),
        'dbname':conninfo_to_dict(venue.runtime_dsn)['dbname']}),venue)
    return venue


@pytest.mark.parametrize('partial',(False,True))
def test_real_v2_unfilled_and_partial_snapshots_use_original_native_source(database,venue_v2,tmp_path,partial):
    if partial:c,accounting,source_codec=setup(database,venue_v2,tmp_path,source_version=2)
    else:
        from trading_system.engine.paper_market_v2 import PaperMatchingModelV2
        c=case(database,venue_v2,tmp_path,scoped_quantities=True)
        c.engine._matching_model=PaperMatchingModelV2(model_id='WORKING_V2_SOURCE',quote_fee_rate='0');submit(c)
        source_codec=None
    service,codec=projector(c,database,tmp_path/'v2-working')
    try:
        if partial:accounting.apply(c.write.command_id)
        result=service.apply(c.write.command_id)
        assert result.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED',result.reasons
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope);order=book.active_orders[0]
            assert order.leaves_quantity==t.Quantity(unit='BASE',value=D('.6' if partial else '1'))
            assert order.filled_quantity.value==D('.4' if partial else '0')
            assert len(book.positions)==(1 if partial else 0)
    finally:
        codec.close()
        if source_codec:source_codec.close()


def test_valid_gtc_observation_after_creation_ttl_never_renews_old_permit(database,venue,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    c=case(database,venue,tmp_path,scoped_quantities=True);submit(c)
    old=c.c.facts.inputs.clock
    c.engine._clock=lambda:replace(old,utc_now=(utc_timestamp(old.utc_now)+timedelta(seconds=10)).isoformat(),
        monotonic_ns=old.monotonic_ns+10**10)
    service,codec=projector(c,database,tmp_path/'expired-creation')
    try:
        assert service.apply(c.write.command_id).status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope).active_orders[0].leaves_quantity.value==1
            assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(1,)
        with pytest.raises(ValueError,match='NO_RETRY'):c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    finally:codec.close()


def test_real_fill_and_snapshot_event_stream_has_distinct_bound_projection_rows(database,venue,tmp_path):
    from integration.test_paper_market_matcher import event
    from trading_system.engine.paper_market import PaperMatcher
    c,accounting,source_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'mixed-stream')
    try:
        accounting.apply(c.write.command_id);service.apply(c.write.command_id)
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        accounting.apply(c.write.command_id);service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope);order=book.active_orders[0];logical=order.order.identity.logical_client_order_id
            assert book.positions[0].actual_quantity.value==D('.6') and order.leaves_quantity.value==D('.4')
            assert order.sequence==2
            events=tx.execute('SELECT sequence,payload FROM ledger.domain_events WHERE scope_id=%s '
                'AND aggregate_id=%s ORDER BY sequence',(scope_id(c.scope),'order:'+logical)).fetchall()
            assert [r[0] for r in events]==[1,2,3,4]
            assert [r[1]['payload'].get('fill_id') is not None for r in events]==[True,False,True,False]
            rows=tx.execute('SELECT sequence,payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
                'ORDER BY sequence',(scope_id(c.scope),'program-order-source:'+logical)).fetchall()
            assert [(r[0],r[1]['applied_event_revision']) for r in rows]==[(1,2),(2,4)]
        service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:assert tx.projections.book(c.scope)==book
    finally:codec.close();source_codec.close()


def test_commit_failure_rolls_back_order_projection_event_book_and_retry_once(database,venue,tmp_path):
    from trading_system.storage.uow import UnitOfWork
    c=case(database,venue,tmp_path,scoped_quantities=True);submit(c)
    service,codec=projector(c,database,tmp_path/'rollback');original=service.uow
    class FailCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,key):return getattr(self.db,key)
        def commit(self):raise RuntimeError('order projection commit failed')
    try:
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
        service.uow=UnitOfWork(lambda:FailCommit(original.connection_factory()))
        with pytest.raises(RuntimeError,match='order projection commit failed'):service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope)==before
            assert tx.execute('SELECT count(*) FROM ledger.order_projections WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.domain_events WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        service.uow=original;assert service.apply(c.write.command_id).status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
    finally:codec.close()


def test_actual_process_restart_does_not_duplicate_projected_working_facts(database,venue,tmp_path):
    import os
    c,accounting,source_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'before-restart')
    try:
        accounting.apply(c.write.command_id);service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
        read_end,write_end=os.pipe();child=os.fork()
        if child==0:
            os.close(read_end)
            try:
                fresh,fresh_codec=projector(c,database,tmp_path/'after-restart')
                result=fresh.apply(c.write.command_id);fresh_codec.close()
                with os.fdopen(write_end,'w') as output:output.write(result.status)
            except BaseException:os._exit(71)
            os._exit(0)
        os.close(write_end)
        with os.fdopen(read_end) as incoming:status=incoming.read(128)
        _,exit_status=os.waitpid(child,0)
        assert os.waitstatus_to_exitcode(exit_status)==0 and status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        with c.service.uow.transaction(c.scope) as tx:assert tx.projections.book(c.scope)==before
    finally:codec.close();source_codec.close()


def test_observed_native_fill_without_per_fill_approval_blocks_new_account_funding(database,venue,tmp_path):
    from integration.test_program_financial_admission import real_case
    c,accounting,source_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'observed-not-ingested')
    try:
        observed=service.observer.observe(c.write.command_id)
        assert observed.submission_known and observed.state=='ACCEPTED'
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
            snapshot=tx.execute('SELECT payload FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND snapshot_id=%s',
                (scope_id(c.scope),observed.source_snapshot_hash)).fetchone()[0]
            assert len(snapshot['normalized_fills'])==1
        b=real_case(database,scoped_quantities=True,venue_account_id=c.scope.venue_account_id,
            collateral_group_id=c.c.terms.collateral_group_id,instrument_uid='must-not-fund-'+uuid4().hex,
            provision_opening=False)
        with pytest.raises(MissingEvidence,match='OBSERVED_PROGRAM_EXECUTIONS_UNPOSTED'):
            case(database,venue,tmp_path,prepared_case=b)
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.execute("SELECT count(*) FROM ledger.intents WHERE payload#>>'{scope,venue_account_id}'=%s",
                (c.scope.venue_account_id,)).fetchone()==(1,)
    finally:codec.close();source_codec.close()


@pytest.mark.parametrize('fault',('unit','fees','global_status'))
def test_new_private_snapshot_conflict_cannot_hide_behind_old_approved_native_identity(database,venue,tmp_path,fault):
    from trading_system.execution.program_fill_source import save_verification_snapshot
    from trading_system.execution.program_account_cash import read_program_account_cash
    from trading_system.execution.program_account_opening import read_program_account_opening
    c,accounting,source_codec=setup(database,venue,tmp_path)
    try:
        accounting.apply(c.write.command_id)
        verified=accounting.source_service.verifier.verify(c.write.command_id)
        # Deliberately disputed source-only candidate, not a legitimate new fill.
        # Existing actual fill/approval/financial rows remain byte-for-byte.
        if fault=='global_status':
            changed=replace(verified,status='DISCREPANCY',discrepancies=('ADVERSARIAL_GLOBAL_SOURCE_CONFLICT',))
        else:
            native=verified.fills[0]
            if fault=='unit':
                economics=replace(native.economics,unit=replace(native.economics.unit,
                    ledger_units_per_native_unit=D(2),native_multiplier=D(2)))
                altered=replace(native,economics=economics)
            else:
                fees=(replace(native.economics.fees[0],amount=native.economics.fees[0].amount+D(1)),)
                altered=replace(native,economics=replace(native.economics,fees=fees),fill=replace(native.fill,fees=fees))
            changed=replace(verified,fills=(altered,))
        with source_uow(database).transaction(c.scope) as tx:save_verification_snapshot(tx,changed)
        reason='OBSERVED_PROGRAM_SOURCE_RECONCILIATION_REQUIRED' if fault=='global_status' else 'OBSERVED_PROGRAM_NATIVE_ECONOMICS_CONFLICT'
        with c.service.uow.transaction(c.scope) as tx:
            with pytest.raises(MissingEvidence,match=reason):read_program_account_cash(tx,read_opening=read_program_account_opening)
            assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
    finally:source_codec.close()


def test_new_observed_clock_same_native_facts_does_not_change_cash_or_owned_quantity(database,venue,tmp_path):
    from datetime import timedelta
    from trading_system.contracts.canonical import utc_timestamp
    from trading_system.execution.program_account_cash import read_program_account_cash
    from trading_system.execution.program_account_opening import read_program_account_opening
    c,accounting,source_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'fresh-clock')
    try:
        accounting.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            before=read_program_account_cash(tx,read_opening=read_program_account_opening);book=tx.projections.book(c.scope)
        old=c.c.facts.inputs.clock
        c.engine._clock=lambda:replace(old,utc_now=(utc_timestamp(old.utc_now)+timedelta(seconds=10)).isoformat(),
            monotonic_ns=old.monotonic_ns+10**10)
        observed=service.observer.observe(c.write.command_id);assert observed.submission_known
        with c.service.uow.transaction(c.scope) as tx:
            assert read_program_account_cash(tx,read_opening=read_program_account_opening)==before
            assert tx.projections.book(c.scope)==book and book.positions[0].actual_quantity.value==D('.4')
    finally:codec.close();source_codec.close()


def test_real_same_account_working_projection_never_writes_the_other_scope(database,venue,tmp_path):
    from integration.test_program_financial_admission import real_case
    def history(c):
        with c.service.uow.transaction(c.scope) as tx:
            return tuple((table,tx.execute('SELECT row_to_json(r)::text,xmin::text FROM ledger.'+table+
                ' r WHERE scope_id=%s ORDER BY row_to_json(r)::text',(scope_id(c.scope),)).fetchall()) for table in
                ('order_projections','book_snapshots','audit_events','positions','cashflows','risk_locks','domain_events','outbox'))
    a=case(database,venue,tmp_path,scoped_quantities=True);submit(a)
    first,codec_a=projector(a,database,tmp_path/'scope-a')
    second=codec_b=None
    try:
        assert first.observer.observe(a.write.command_id).state=='ACCEPTED'
        b=case(database,venue,tmp_path,prepared_case=real_case(database,scoped_quantities=True,
            venue_account_id=a.scope.venue_account_id,collateral_group_id=a.c.terms.collateral_group_id,
            instrument_uid='working-peer-'+uuid4().hex))
        submit(b);second,codec_b=projector(b,database,tmp_path/'scope-b')
        before_b=history(b)
        assert first.apply(a.write.command_id).status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        assert history(b)==before_b
        before_a=history(a)
        assert second.apply(b.write.command_id).status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED'
        assert history(a)==before_a
        for c in (a,b):
            with c.service.uow.transaction(c.scope) as tx:
                book=tx.projections.book(c.scope)
                assert book.positions==() and book.active_orders[0].leaves_quantity.value==1
                assert tx.reservations.for_intent(c.bundle.intent.intent_id)
    finally:
        codec_a.close()
        if codec_b:codec_b.close()


def test_owned_plus_real_working_leaves_new_bounded_intake_retains_cost_block_and_replay_stable(database,venue,tmp_path):
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
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
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
            # Quantity facts cover the requested target, but the existing core
            # still requires complete cost evidence even for a no-action plan.
            assert raw.positions[0].actual_quantity.value+raw.active_orders[0].leaves_quantity.value==D(1)
            with pytest.raises(ValueError,match='POSITION_EVIDENCE'):
                compile_program_target(facts.compilation,financial,scoped)
            assert tx.intents.get(intake.intent_id) is None and tx.reservations.for_intent(intake.intent_id)==()
            assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',
                (scope_id(c.scope),)).fetchone()==(1,)
            assert tx.risk_locks.active(c.scope) and book.positions[0].cost_evidence.missing
    finally:codec.close();old_codec.close()


def test_unknown_query_keeps_actual_leaves_and_denies_current_working_authority(database,venue,tmp_path):
    from trading_system.execution.program_order_projection import read_program_working_inputs
    c,accounting,old_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'unknown')
    try:
        accounting.apply(c.write.command_id);service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:before=tx.projections.book(c.scope)
        c.engine.query_creation=lambda _:None
        assert service.apply(c.write.command_id).status=='SOURCE_BLOCKED'
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope)==before and before.active_orders[0].leaves_quantity.value==D('.6')
            with pytest.raises(MissingEvidence,match='PROGRAM_ORDER_QUERY_UNKNOWN'):read_program_working_inputs(tx,before)
    finally:codec.close();old_codec.close()


def test_owner_authored_order_quantities_cannot_replace_private_source(database,venue,tmp_path):
    from trading_system.execution.program_order_projection import read_program_working_inputs
    c,accounting,old_codec=setup(database,venue,tmp_path)
    service,codec=projector(c,database,tmp_path/'forged')
    try:
        accounting.apply(c.write.command_id);service.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope);order=book.active_orders[0]
            forged=replace(book,sequence=book.sequence+1,active_orders=(replace(order,
                leaves_quantity=replace(order.leaves_quantity,value=D(0))),))
            tx.projections.save_book(forged,book.sequence)
            from trading_system.execution.program_book_cash import bind_program_book_cash
            from trading_system.execution.program_legacy_opening import read_program_local_account_opening
            bind_program_book_cash(tx,forged,read_opening=read_program_local_account_opening)
            with pytest.raises(MissingEvidence,match='PROGRAM_ORDER_BOOK_MISMATCH'):read_program_working_inputs(tx,forged)
        with pytest.raises(MissingEvidence,match='PROGRAM_ORDER_BOOK_MISMATCH'):service.apply(c.write.command_id)
    finally:codec.close();old_codec.close()


def test_later_owner_bundle_revision_cannot_rescale_original_native_order(database,venue,tmp_path):
    c=case(database,venue,tmp_path,scoped_quantities=True);submit(c)
    service,codec=projector(c,database,tmp_path/'later-bundle')
    try:
        from trading_system.risk.program_financial_admission import BUNDLE_PREFIX
        # Ordinary audit revisions do not replace the original protected bundle.
        changed=replace(c.bundle,intent=replace(c.bundle.intent,unit=replace(c.bundle.intent.unit,
            ledger_units_per_native_unit=D(2),native_multiplier=D(2))))
        with c.service.uow.transaction(c.scope) as tx:
            tx._append('audit_events',BUNDLE_PREFIX+c.bundle.intent.intent_id,changed,1)
        result=service.apply(c.write.command_id)
        assert result.status=='ORDER_BOOK_PROJECTED_DUTIES_RETAINED',result.reasons
        with c.service.uow.transaction(c.scope) as tx:
            assert tx.projections.book(c.scope).active_orders[0].leaves_quantity==t.Quantity(unit='BASE',value=D(1))
    finally:codec.close()
