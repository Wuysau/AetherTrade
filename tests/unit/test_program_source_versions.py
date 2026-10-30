"""Static version routing/math only; no source, owner or execution qualification."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from trading_system.contracts.canonical import domain_payload
from unit.test_program_asset_effects import arithmetic_fill


@pytest.mark.parametrize('tag',[
    dict(record_kind='NORMALIZED_PAPER_FILL_V99',contract_version='2026-10-30'),
    dict(record_kind='NORMALIZED_PAPER_FILL_V2',contract_version='2099-01-01'),
    dict(contract_version='2026-10-30'),
    dict(source_v2={}),
])
def test_unknown_or_partial_normalized_tag_never_falls_back_to_v1(tag):
    from trading_system.execution.program_fill_source import decode_source_normalized
    stored={**domain_payload(arithmetic_fill()),**tag}
    with pytest.raises(ValueError,match='PROGRAM_NORMALIZED_SOURCE_VERSION_REQUIRED'):
        decode_source_normalized(stored,{})


def test_v2_math_keeps_native_economics_without_projecting_to_v1():
    from trading_system.execution.paper_program_v2_reports import NormalizedPaperFillV2,PaperFillSourceV2
    from trading_system.execution.program_asset_effects import derive_spot_asset_effects
    old=arithmetic_fill()
    provenance=PaperFillSourceV2(node_id='math',client_id='math',accepted_context_hash='a'*64,
        consumption_context_hash='b'*64,causal_graph_hash='c'*64,causal_input_ids=(),parent_client_id=None)
    normalized=NormalizedPaperFillV2(fill=old.fill,side=old.side,economics=old.economics,raw=None,
        fees_known=True,source_v2=provenance)
    effects=derive_spot_asset_effects(normalized,base_asset='BTC',quote_asset='USDT')
    assert effects.position_delta.value==D('.4') and not effects.balance_credit_authorized
