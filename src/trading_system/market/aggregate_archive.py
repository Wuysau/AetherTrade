"""Request-bound public response archives using the existing snapshot backend.

Commit acknowledgement is not source authentication, window completeness or
platform crash-durability qualification. Original bytes are committed before
parsing; no account scope, fill, financial record or send authority is created.
"""
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from trading_system.contracts.canonical import canonical_json,domain_payload,utc_timestamp
from .snapshots import LocalSnapshotBackend
from .provenance import is_digest
from .aggregate_requests import AggregateTradeRequest
from .aggregate_trades import parse_aggregate_trade_page


def need(condition):
    if not condition:raise ValueError('AGGREGATE_ARCHIVE_BINDING_INVALID')


def _manifest(request,payload_hash,received_at):
    return canonical_json(dict(contract_version='2026-10-31',record_kind='PUBLIC_AGGREGATE_ARCHIVE_V1',
        request=domain_payload(request),request_hash=request.identity_hash,
        payload_hash=payload_hash,received_at=received_at)).encode('utf-8')


@dataclass(frozen=True,slots=True,kw_only=True)
class AggregateArchiveReceipt:
    request_hash:str
    payload_hash:str
    manifest_hash:str
    received_at:str

    @property
    def source_verified(self)->Literal[False]:return False
    @property
    def execution_authorized(self)->Literal[False]:return False


class AggregateTradeArchive:
    def __init__(self,backend):
        need(type(backend) is LocalSnapshotBackend)
        self.backend=backend

    def _commit(self,key,data):
        try:previous=self.backend.read_bounded(key,len(data))
        except FileNotFoundError:pass
        else:need(previous==data)
        staged=self.backend.stage(key,data)
        try:staged.commit()
        except BaseException:
            staged.abort()
            raise

    def record(self,request,payload,*,received_at):
        need(type(request) is AggregateTradeRequest)
        request.__post_init__()
        need(type(received_at) is str and len(received_at)<=64)
        utc_timestamp(received_at)
        need(type(payload) is bytes and len(payload)<=1_048_576)
        raw_hash=sha256(payload).hexdigest()
        manifest=_manifest(request,raw_hash,received_at)
        need(len(manifest)<=65536)
        self._commit(raw_hash,payload)
        manifest_hash=sha256(manifest).hexdigest()
        self._commit(manifest_hash,manifest)
        return AggregateArchiveReceipt(request_hash=request.identity_hash,payload_hash=raw_hash,
            manifest_hash=manifest_hash,received_at=received_at)

    def read_payload(self,request,receipt):
        need(type(request) is AggregateTradeRequest and type(receipt) is AggregateArchiveReceipt)
        request.__post_init__()
        need(type(receipt.received_at) is str and len(receipt.received_at)<=64)
        utc_timestamp(receipt.received_at)
        need(all(is_digest(v) for v in (receipt.request_hash,receipt.payload_hash,receipt.manifest_hash)))
        need(receipt.request_hash==request.identity_hash)
        manifest=self.backend.read_bounded(receipt.manifest_hash,65536)
        need(len(manifest)<=65536 and sha256(manifest).hexdigest()==receipt.manifest_hash
            and manifest==_manifest(request,receipt.payload_hash,receipt.received_at))
        raw=self.backend.read_bounded(receipt.payload_hash,1_048_576)
        need(len(raw)<=1_048_576 and sha256(raw).hexdigest()==receipt.payload_hash)
        return raw

    def read_page(self,request,receipt):
        return parse_aggregate_trade_page(self.read_payload(request,receipt),product=request.product,
            instrument_uid=request.instrument_uid,native_unit=request.native_unit)
