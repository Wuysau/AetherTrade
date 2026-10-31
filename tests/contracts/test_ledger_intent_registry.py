"""Explicit ledger kind/version dispatch preserves historical wire records."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from trading_system.contracts.wire import ContractCodec


def test_legacy_codec_retains_exact_content_and_hash():
    from trading_system.contracts.ledger_intents import LedgerIntentCodec
    payload=json.loads((Path(__file__).parents[1]/'fixtures/contracts/examples/trade-intent.paper.json').read_text(encoding='utf-8'))
    old=ContractCodec().decode('trade-intent',payload)
    codec=LedgerIntentCodec()
    assert codec.decode(payload)==old
    assert codec.encode(old)==ContractCodec().encode(old)
    assert codec.hash(old)==ContractCodec().hash(old)


@pytest.mark.parametrize('change',[{'contract_version':'future'},{'record_kind':'UNKNOWN'},
    {'record_kind':'PROGRAM_FINANCIAL_INTENT'},{'contract_version':'2026-10-29'}])
def test_unknown_or_ambiguous_ledger_kind_never_falls_back(change):
    from trading_system.contracts.ledger_intents import LedgerIntentCodec
    payload=json.loads((Path(__file__).parents[1]/'fixtures/contracts/examples/trade-intent.paper.json').read_text(encoding='utf-8'))
    with pytest.raises(ValueError):LedgerIntentCodec().decode({**payload,**change})
