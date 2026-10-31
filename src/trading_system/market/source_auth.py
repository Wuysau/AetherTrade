"""Aggregate public-data source authentication port.

Transport provenance is recorded but never self-authenticating. A receipt is
`source_verified=True` only after an explicitly injected verifier accepts the
TLS peer, origin, host, status and byte hashes. Missing verifier or provenance
keeps the result a candidate; no VP admission is granted from that state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from trading_system.contracts.canonical import canonical_hash


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportProvenance:
    origin: str
    resolved_ip: str
    tls_verified: bool
    tls_peer_certificate_sha256: str
    http_status: int
    response_headers_hash: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateSourceEvidence:
    request_hash: str
    manifest_hash: str
    payload_hash: str
    provenance: TransportProvenance
    source_verified: bool
    verifier_id: str | None = None

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def authenticate_aggregate_receipt(receipt, provenance: TransportProvenance, *,
                                   verifier: Callable | None = None) -> AggregateSourceEvidence:
    if receipt is None or not getattr(receipt, 'request_hash', None) \
            or not getattr(receipt, 'manifest_hash', None) \
            or not getattr(receipt, 'payload_hash', None):
        raise ValueError('AGGREGATE_RECEIPT_REQUIRED')
    if type(provenance) is not TransportProvenance:
        raise ValueError('AGGREGATE_PROVENANCE_REQUIRED')
    if provenance.http_status != 200 or not provenance.tls_verified \
            or not provenance.tls_peer_certificate_sha256 \
            or not provenance.response_headers_hash:
        raise ValueError('AGGREGATE_SOURCE_PROVENANCE_INVALID')
    if verifier is None:
        return AggregateSourceEvidence(
            request_hash=receipt.request_hash, manifest_hash=receipt.manifest_hash,
            payload_hash=receipt.payload_hash, provenance=provenance,
            source_verified=False, verifier_id=None)
    verifier_id = verifier(request_hash=receipt.request_hash,
                           manifest_hash=receipt.manifest_hash,
                           payload_hash=receipt.payload_hash, provenance=provenance)
    if type(verifier_id) is not str or not verifier_id:
        raise ValueError('AGGREGATE_SOURCE_VERIFIER_REFUSED')
    return AggregateSourceEvidence(
        request_hash=receipt.request_hash, manifest_hash=receipt.manifest_hash,
        payload_hash=receipt.payload_hash, provenance=provenance,
        source_verified=True, verifier_id=verifier_id)


def provenance_hash(provenance: TransportProvenance) -> str:
    if type(provenance) is not TransportProvenance:
        raise ValueError('AGGREGATE_PROVENANCE_REQUIRED')
    return canonical_hash({'origin': provenance.origin,
                           'resolved_ip': provenance.resolved_ip,
                           'tls_verified': provenance.tls_verified,
                           'tls_peer_certificate_sha256': provenance.tls_peer_certificate_sha256,
                           'http_status': provenance.http_status,
                           'response_headers_hash': provenance.response_headers_hash})
