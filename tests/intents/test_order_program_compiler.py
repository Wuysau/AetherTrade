from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_snapshots import OrderRuleSnapshot,OrderCapabilitySnapshot
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.order_program_compiler import compile_atomic_target
from trading_system.intents.semantic_validation import ProposalRejected

def fixture():
 scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='isolated',product=t.Product.SPOT,instrument_uid='paper:BTCUSDT',venue_leg='BOTH')
 proof=t.EvidenceRef(id='fixture',hash='b'*64,environment=t.Environment.PAPER,source='SYNTHETIC_TEST',visible_at='2026-10-28T09:00:00Z')
 rules=OrderRuleSnapshot(contract_version='2026-10-28',snapshot_id='rules:fixture',scope=scope,venue='BINANCE',revision=1,tick_size='0.01',quantity_step='0.000001',minimum_quantity='0.000001',minimum_notional='10',quote_asset='USDT',base_asset='BTC',allowed_tifs=('GTC','IOC','FOK'),maximum_callback_percent=None,evidence=(proof,),as_of='2026-10-28T09:00:00Z',valid_until='2026-10-28T11:00:00Z')
 cap=OrderCapabilitySnapshot(contract_version='2026-10-28',snapshot_id='cap:fixture',scope=scope,venue='BINANCE',adapter_build_digest='a'*64,transport='PAPER',rule_revision=1,capability_ids=('SPOT-LIMIT','SPOT-TIF'),qualification='NOT_RUN',evidence=(),as_of=rules.as_of,valid_until=rules.valid_until)
 program=json.loads(Path('tests/fixtures/orders-v2/program.paper.json').read_text())
 value=dict(contract_version='2026-10-28',proposal_id='limit-fixture',instrument_ref=scope.instrument_uid,action='SET_TARGET',target_side='LONG',target_sizing='QUOTE_NOTIONAL',target_value='4000',requested_leverage=1,program=program,protection_policy='NATIVE_REQUIRED',exit_plan=dict(stop_price='81330.00',stop_trigger='LAST',review_due_at='2026-10-28T11:00:00Z',maximum_holding_seconds=3600,thesis_invalidation='Fixture stop'),valid_until=program['submit_valid_until'],evidence_refs=['fixture'],summary='Fixture')
 proposal=VersionedOrderCodec().decode('order-proposal',value)
 kwargs=dict(expected_instrument_ref=scope.instrument_uid,scope=scope,now='2026-10-28T10:00:00Z',expected_build_digest='a'*64,expected_rule_revision=1,expected_capabilities_hash=canonical_hash(domain_payload(cap)),allow_round_down=True)
 return proposal,rules,cap,kwargs

def test_limit_target_uses_limit_price_not_market_or_budget_ceiling():
 p,r,c,k=fixture();result=compile_atomic_target(p,r,c,**k)
 assert result.order_type=='LIMIT' and result.limit_price=='81490.00'
 assert result.original_proposal.exit_plan.stop_price=='81330.00'
 assert Decimal(result.normalized_target_quantity)*Decimal(result.limit_price)<=4000
 assert not result.execution_authorized
 assert result.original_program==p.program


def test_rule_revision_change_requires_replan():
 p,r,c,k=fixture()
 with pytest.raises(ProposalRejected):compile_atomic_target(p,replace(r,revision=2),c,**k)


def test_cash_short_leg_is_not_compiled_as_long_buy():
 p,r,c,k=fixture();node=p.program.nodes[0]
 p=replace(p,program=replace(p.program,nodes=(replace(node,order=replace(node.order,position_side='SHORT')),)))
 with pytest.raises(ProposalRejected):compile_atomic_target(p,r,c,**k)
