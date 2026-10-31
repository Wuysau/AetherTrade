from dataclasses import replace
from hashlib import sha256
import pytest
from test_aggregate_trade_request import request
from trading_system.market.snapshots import LocalSnapshotBackend
from trading_system.market.aggregate_archive import AggregateTradeArchive

STAMP='2026-10-31T00:00:00Z'
PAYLOAD=b'[{"a":1,"p":"100","q":"0.1000","f":1,"l":1,"T":1000,"m":true}]'

def test_restart_replays_original_bytes_and_binds_request(tmp_path):
    r=request();archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path))
    receipt=archive.record(r,PAYLOAD,received_at=STAMP)
    fresh=AggregateTradeArchive(LocalSnapshotBackend(tmp_path))
    assert fresh.read_payload(r,receipt)==PAYLOAD
    assert fresh.read_page(r,receipt).rows[0].quantity=='0.1000'
    assert not receipt.source_verified and not receipt.execution_authorized
    with pytest.raises(ValueError):fresh.read_payload(replace(r,venue_symbol='ETHUSDT'),receipt)

def test_malformed_response_is_archived_before_decoder_failure(tmp_path):
    archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path));r=request()
    receipt=archive.record(r,b'bad-json',received_at=STAMP)
    assert archive.read_payload(r,receipt)==b'bad-json'
    with pytest.raises(ValueError):archive.read_page(r,receipt)

@pytest.mark.parametrize('phase',[1,2])
def test_failed_commit_returns_no_archive_receipt(tmp_path,monkeypatch,phase):
    backend=LocalSnapshotBackend(tmp_path);original=backend.stage;calls=[]
    def stage(key,data):
        result=original(key,data);calls.append(key)
        if len(calls)==phase:
            class Broken:
                def commit(self):raise OSError('commit failed')
                def abort(self):result.abort()
            return Broken()
        return result
    monkeypatch.setattr(backend,'stage',stage)
    with pytest.raises(OSError):AggregateTradeArchive(backend).record(request(),PAYLOAD,received_at=STAMP)

def test_tampered_content_cannot_be_read_or_silently_repaired(tmp_path):
    archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path));r=request()
    receipt=archive.record(r,PAYLOAD,received_at=STAMP)
    (tmp_path/sha256(PAYLOAD).hexdigest()).write_bytes(b'tampered')
    with pytest.raises(ValueError):archive.read_payload(r,receipt)
    with pytest.raises(ValueError):archive.record(r,PAYLOAD,received_at=STAMP)



def test_untrusted_receipt_cannot_select_a_filesystem_path(tmp_path,monkeypatch):
    archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path));r=request()
    receipt=archive.record(r,PAYLOAD,received_at=STAMP)
    def forbidden(key):raise AssertionError('invalid receipt reached backend read')
    monkeypatch.setattr(archive.backend,'read',forbidden)
    with pytest.raises(ValueError):archive.read_payload(r,replace(receipt,manifest_hash='../private'))


@pytest.mark.parametrize('operation',['read','record'])
def test_oversized_archive_is_rejected_without_unbounded_backend_read(tmp_path,monkeypatch,operation):
    backend=LocalSnapshotBackend(tmp_path);archive=AggregateTradeArchive(backend);r=request()
    receipt=archive.record(r,PAYLOAD,received_at=STAMP)
    (tmp_path/receipt.payload_hash).write_bytes(b'x'*(1_048_576+1))
    def forbidden(key):raise AssertionError('unbounded backend read used')
    monkeypatch.setattr(backend,'read',forbidden)
    with pytest.raises(ValueError):
        if operation=='read':archive.read_payload(r,receipt)
        else:archive.record(r,PAYLOAD,received_at=STAMP)


def test_oversized_metadata_never_publishes_a_receipt_or_raw_object(tmp_path):
    archive=AggregateTradeArchive(LocalSnapshotBackend(tmp_path))
    with pytest.raises(ValueError):archive.record(request(),PAYLOAD,
        received_at='2026-10-31T00:00:00.'+'1'*70000+'Z')
    assert list(tmp_path.iterdir())==[]
