from dataclasses import replace

import pytest

from trading_system.contracts import types as t
from trading_system.execution.evidence import EvidenceIngestor
from trading_system.storage.models import ScopeMismatch


def test_foreign_scope_is_rejected_before_reading_any_archive_path():
    scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='account',product=t.Product.SPOT,
        instrument_uid='asset',venue_leg='BOTH')
    def forbidden(ref):raise AssertionError('Foreign metadata must not reach archive IO')
    ingestor=EvidenceIngestor(None,scope,read_raw=forbidden)
    ref=t.EvidenceRef(id='must-not-read',hash='a'*64,environment=t.Environment.PAPER,
        source='synthetic',visible_at='2026-10-30T00:00:00Z')
    raw=t.RawVenueMessage(scope=replace(scope,venue_account_id='foreign'),source='synthetic',payload_hash=ref.hash,
        raw_payload_ref=ref,received_at=ref.visible_at)
    with pytest.raises(ScopeMismatch):ingestor.ingest_raw(raw,decoder=lambda _:None)
