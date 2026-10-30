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
    verifier,codec=actual_verifier(c,path)
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


def test_owned_plus_real_working_leaves_new_bounded_intake_is_no_action_and_replay_stable(database,venue,tmp_path):
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
            result=compile_program_target(facts.compilation,financial,scoped)
            assert result.delta.actual.value==D('.4') and result.delta.working.value==D('.6')
            assert result.delta.remaining.value==0 and result.blockers==('TARGET_ALREADY_SATISFIED',) and not result.requests
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
    finally:codec.close();old_codec.close()
