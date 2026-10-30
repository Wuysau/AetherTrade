"""Unchanged 2026-10-28 legacy artifact compatibility, not new execution proof.

The historical demo explicitly used synthetic venue events and omitted fees.
It cannot supply missing native-source/owner/permit qualification for new code.
"""
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from trading_system.contracts import types as t
from trading_system.execution.program_account_cash import _legacy_effects
from trading_system.storage.models import decode,MissingEvidence


def test_actual_committed_legacy_history_with_unknown_fees_cannot_create_mixed_cash_credit():
    path=Path(__file__).resolve().parents[2]/'docs/evidence/2026-10-28-real-demo/raw.zip'
    with ZipFile(path) as archive:
        original=archive.read('observations/real-demo-ledger-owner-flat.json')
    ledger=json.loads(original)['ledger']
    assert len(ledger['fills'])==3
    for row in ledger['fills']:
        fill=decode(t.Fill,row['payload'])
        commands=tuple(c['payload'] for c in ledger['commands'] if c['sequence']==1
            and c['payload']['logical_client_order_id']==fill.order.logical_client_order_id)
        assert len(commands)==1 and fill.fees==()
        assert fill.venue=='PAPER_SYNTHETIC_NOT_EXCHANGE_EVIDENCE'
        with pytest.raises(MissingEvidence,match='LEGACY_SETTLEMENT_OR_FEES_UNVERIFIED'):
            _legacy_effects(object(),fill,commands)
    # Raw archive data is consumed as-is; no new fee, evidence wrapper or source
    # attestation was generated to make this compatibility contract pass.
    with ZipFile(path) as archive:
        assert archive.read('observations/real-demo-ledger-owner-flat.json')==original
