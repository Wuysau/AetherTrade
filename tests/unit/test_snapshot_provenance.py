"""Offline snapshot/provenance assertions; no T01-T66 scenario is registered.

These tests exercise the production modules with synthetic local data only.
Passing them would not be native/platform evidence and does not enable LIVE.
"""
from dataclasses import fields, replace
from datetime import timedelta

import pytest

from trading_system.contracts import types as t
from trading_system.market.freshness import FreshnessPolicy
from trading_system.market.provenance import (
    NewsProvenance,
    NewsProvenanceError,
    UntrustedNewsView,
)
from trading_system.market.snapshots import (
    SnapshotIntegrityError,
    SnapshotNotFoundError,
    SnapshotPersistenceError,
    snapshot_content_hash,
)
from unit.support_ip_05 import (
    NOW,
    iso_ago,
    iso_at,
    make_clock,
    make_raw_news,
    make_scope,
    make_session,
    make_snapshot,
    snapshot_case,
)


def test_snapshot_ref_after_durable_write(snapshot_case):
    case = snapshot_case
    result = case.fail_object_commit()
    assert result.published_refs == []
    assert isinstance(result.error, SnapshotPersistenceError)
    assert case.store.published_refs == []
    assert case.published == []

    ref = case.put()
    assert ref.id == ref.hash
    assert ref.hash == snapshot_content_hash(case.packet)
    assert ref.visible_at == iso_at(NOW)
    assert case.store.published_refs == [ref]
    assert case.published == [ref]

    expected = replace(case.packet, ref=ref)
    assert case.store.load(ref) == expected

    # Content addressing makes a repeated put idempotent; no second publication.
    assert case.put() == ref
    assert case.store.published_refs == [ref]
    assert case.published == [ref]


def test_snapshot_reference_is_not_published_when_catalog_rejects_it(snapshot_case):
    case = snapshot_case
    result = case.fail_reference_publish()
    assert result.published_refs == []
    assert isinstance(result.error, SnapshotPersistenceError)
    assert case.store.published_refs == []
    assert case.published == []

    # The object may exist, but without the published reference it is not loadable.
    digest = snapshot_content_hash(case.packet)
    orphan = t.SnapshotRef(id=digest, hash=digest,
                           environment=t.Environment.PAPER,
                           source='offline-fixture', visible_at=iso_at(NOW))
    with pytest.raises(SnapshotNotFoundError):
        case.store.load(orphan)

    case.publish_failure = False
    ref = case.put()
    assert case.store.load(ref) == replace(case.packet, ref=ref)


def test_snapshot_rejects_tampering_and_unpublished_references(snapshot_case):
    case = snapshot_case
    ref = case.put()
    case.backend.objects[ref.hash] = b'{"ref":{}}'
    with pytest.raises(SnapshotIntegrityError):
        case.store.load(ref)

    case = type(case)()
    digest = snapshot_content_hash(case.packet)
    case.backend.stage(digest, b'{}').commit()
    orphan = t.SnapshotRef(id=digest, hash=digest,
                           environment=t.Environment.PAPER,
                           source='offline-fixture', visible_at=iso_at(NOW))
    with pytest.raises(SnapshotNotFoundError):
        case.store.load(orphan)


def test_snapshot_rejects_wrong_hash_future_content_and_cross_environment_ref(snapshot_case):
    case = snapshot_case
    bad_hash = replace(case.packet, ref=replace(case.packet.ref, hash='f' * 64))
    with pytest.raises(SnapshotIntegrityError):
        case.store.put(bad_hash)
    assert case.store.published_refs == []

    future = make_snapshot(occurred_ms_ago=-2_000, received_ms_ago=-1_500,
                           published_ms_ago=-2_500, visible_ms_ago=-2_600)
    with pytest.raises(SnapshotIntegrityError):
        case.store.put(future)
    assert case.store.published_refs == []

    ref = case.put()
    with pytest.raises(SnapshotIntegrityError):
        case.store.load(replace(ref, environment=t.Environment.LIVE))


def test_future_and_stale_data():
    policy = FreshnessPolicy()
    scope = make_scope()
    session = make_session(scope)
    clock = make_clock()

    fresh = make_snapshot(scope_key=scope, occurred_ms_ago=500, received_ms_ago=400,
                          published_ms_ago=350, visible_ms_ago=300)
    verdict = policy.evaluate(fresh, clock, session)
    assert verdict.usable is True
    assert verdict.age_ms == 500
    assert verdict.rejection_codes == ()
    assert verdict.missing == ()

    stale = make_snapshot(scope_key=scope, occurred_ms_ago=5_000, received_ms_ago=4_900,
                          published_ms_ago=4_800, visible_ms_ago=4_700)
    stale_verdict = policy.evaluate(stale, clock, session)
    assert stale_verdict.usable is False
    assert stale_verdict.age_ms == 5_000
    assert 'STALE_QUOTE' in stale_verdict.rejection_codes

    future = make_snapshot(scope_key=scope, occurred_ms_ago=-1_000, received_ms_ago=-1_200,
                           published_ms_ago=-1_300, visible_ms_ago=-1_400,
                           ref_visible_ms_ago=-1_400)
    future_verdict = policy.evaluate(future, clock, session)
    assert future_verdict.usable is False
    assert future_verdict.age_ms == -1_000
    assert 'FUTURE_DATA' in future_verdict.rejection_codes

    received_in_future = make_snapshot(scope_key=scope, occurred_ms_ago=500,
                                       received_ms_ago=-100, published_ms_ago=-50,
                                       visible_ms_ago=-10, ref_visible_ms_ago=-10)
    assert not policy.evaluate(received_in_future, clock, session).usable
    assert 'FUTURE_DATA' in policy.evaluate(received_in_future, clock,
                                            session).rejection_codes

    degraded = policy.evaluate(fresh, make_clock(health='DEGRADED'), session)
    assert degraded.usable is False
    assert 'CLOCK_UNHEALTHY' in degraded.rejection_codes
    invalid_clock = policy.evaluate(fresh, make_clock(monotonic_ns=0), session)
    assert invalid_clock.usable is False
    assert 'CLOCK_INVALID' in invalid_clock.rejection_codes

    missing_bid = policy.evaluate(make_snapshot(scope_key=scope, bid=None), clock, session)
    assert missing_bid.usable is False
    assert 'MISSING_BID' in missing_bid.rejection_codes
    assert 'bid' in missing_bid.missing

    missing_coverage = policy.evaluate(
        make_snapshot(scope_key=scope, coverage_missing=('private_positions',)),
        clock, session)
    assert missing_coverage.usable is False
    assert 'MISSING_COVERAGE' in missing_coverage.rejection_codes
    assert 'private_positions' in missing_coverage.missing

    wrong_hash = replace(fresh, ref=replace(fresh.ref, hash='0' * 64))
    hash_verdict = policy.evaluate(wrong_hash, clock, session)
    assert hash_verdict.usable is False
    assert 'SNAPSHOT_HASH_MISMATCH' in hash_verdict.rejection_codes

    wrong_session = make_session(scope, instrument_uid='binance:spot:ETHUSDT')
    session_verdict = policy.evaluate(fresh, clock, wrong_session)
    assert session_verdict.usable is False
    assert 'SESSION_MISMATCH' in session_verdict.rejection_codes

    # The crypto reference gate is inclusive at 2 seconds and excludes beyond it.
    boundary = make_snapshot(scope_key=scope, occurred_ms_ago=2_000,
                             received_ms_ago=1_900, published_ms_ago=1_800,
                             visible_ms_ago=1_700)
    assert policy.evaluate(boundary, clock, session).usable is True
    beyond = make_snapshot(scope_key=scope, occurred_ms_ago=2_001,
                           received_ms_ago=1_900, published_ms_ago=1_800,
                           visible_ms_ago=1_700)
    assert not policy.evaluate(beyond, clock, session).usable


def test_news_is_untrusted():
    archive = NewsProvenance(clock=lambda: NOW)
    raw = make_raw_news()
    downloaded_at = iso_ago(60_000)
    record = archive.record(raw, downloaded_at=downloaded_at)

    assert record.news_id == raw.news_id
    assert record.source == raw.source
    assert record.published_at == raw.published_at
    assert record.revised_at == raw.revised_at
    assert record.downloaded_at == downloaded_at
    assert record.revision == 2
    assert len(record.content_hash) == 64
    assert record.untrusted is True
    assert record.classification == 'UNTRUSTED_DATA'

    view = archive.view(record)
    assert view == UntrustedNewsView(
        news_id=record.news_id, source=record.source, title=record.title,
        body=record.body, published_at=record.published_at,
        revised_at=record.revised_at, downloaded_at=record.downloaded_at,
        revision=record.revision, content_hash=record.content_hash)
    assert view.classification == 'UNTRUSTED_DATA'
    assert view.untrusted is True
    assert {field.name for field in fields(view)} == {
        'news_id', 'source', 'title', 'body', 'published_at', 'revised_at',
        'downloaded_at', 'revision', 'content_hash', 'classification',
    }
    for forbidden in ('authorization', 'scopes', 'capabilities', 'allowed',
                      'credential_refs'):
        assert not hasattr(view, forbidden)
        assert not hasattr(record, forbidden)
    assert not hasattr(archive, 'authorize')

    newer = archive.record(
        replace(raw, revision=3, revised_at=iso_ago(30_000), body='Revised note.'),
        downloaded_at=iso_ago(10_000))
    assert archive.revisions('news-1') == (record, newer)
    assert archive.model_view('news-1') == archive.view(newer)
    assert archive.model_view('news-1', 2) == view

    with pytest.raises(NewsProvenanceError):
        archive.record(replace(raw, body='Rewritten in place.'), downloaded_at=downloaded_at)
    with pytest.raises(NewsProvenanceError):
        archive.record(make_raw_news(news_id='news-2'),
                       downloaded_at=iso_at(NOW + timedelta(seconds=1)))
    with pytest.raises(NewsProvenanceError):
        archive.record(make_raw_news(news_id='news-3', published_ms_ago=1_000,
                                     revised_ms_ago=None, revision=1),
                       downloaded_at=iso_ago(60_000))
    with pytest.raises(NewsProvenanceError):
        archive.record(make_raw_news(news_id='news-4', revised_ms_ago=None, revision=2),
                       downloaded_at=iso_ago(60_000))


def test_sessions_not_24x7():
    equity = make_scope(product=t.Product.US_EQUITY)
    crypto = make_scope()
    clock = make_clock()
    policy = FreshnessPolicy(age_limits_ms={t.Product.SPOT: 2_000,
                                            t.Product.US_EQUITY: 30_000})

    equity_quote = make_snapshot(scope_key=equity, occurred_ms_ago=10_000,
                                 received_ms_ago=9_900, published_ms_ago=9_800,
                                 visible_ms_ago=9_700)
    open_verdict = policy.evaluate(equity_quote, clock, make_session(equity))
    assert open_verdict.usable is True
    assert open_verdict.age_ms == 10_000

    crypto_quote = make_snapshot(scope_key=crypto, occurred_ms_ago=10_000,
                                 received_ms_ago=9_900, published_ms_ago=9_800,
                                 visible_ms_ago=9_700)
    crypto_verdict = policy.evaluate(crypto_quote, clock, make_session(crypto))
    assert crypto_verdict.usable is False
    assert 'STALE_QUOTE' in crypto_verdict.rejection_codes

    closed = policy.evaluate(equity_quote, clock, make_session(equity, state='CLOSED'))
    assert closed.usable is False
    assert 'SESSION_CLOSED' in closed.rejection_codes
    assert 'STALE_QUOTE' not in closed.rejection_codes

    halted = policy.evaluate(equity_quote, clock, make_session(equity, state='HALTED'))
    assert halted.usable is False
    assert 'SESSION_HALTED' in halted.rejection_codes
    assert 'STALE_QUOTE' not in halted.rejection_codes

    unknown = policy.evaluate(equity_quote, clock, make_session(equity, state='UNKNOWN'))
    assert unknown.usable is False
    assert 'SESSION_UNKNOWN' in unknown.rejection_codes

    # No implicit crypto 2-second gate is invented for securities.
    default = FreshnessPolicy()
    default_verdict = default.evaluate(equity_quote, clock, make_session(equity))
    assert default_verdict.usable is False
    assert 'FRESHNESS_POLICY_MISSING' in default_verdict.rejection_codes
    assert 'STALE_QUOTE' not in default_verdict.rejection_codes

    closed_policy = FreshnessPolicy(age_limits_ms={t.Product.SPOT: 2_000},
                                    closed_state_max_age_ms=60_000)
    recent_closed = closed_policy.evaluate(
        make_snapshot(scope_key=equity, occurred_ms_ago=30_000,
                      received_ms_ago=29_900, published_ms_ago=29_800,
                      visible_ms_ago=29_700),
        clock, make_session(equity, state='CLOSED'))
    assert recent_closed.usable is False
    assert 'SESSION_CLOSED' in recent_closed.rejection_codes
    assert 'STALE_QUOTE' not in recent_closed.rejection_codes

    old_closed = closed_policy.evaluate(
        make_snapshot(scope_key=equity, occurred_ms_ago=120_000,
                      received_ms_ago=119_900, published_ms_ago=119_800,
                      visible_ms_ago=119_700),
        clock, make_session(equity, state='CLOSED'))
    assert old_closed.usable is False
    assert 'SESSION_CLOSED' in old_closed.rejection_codes
    assert 'STALE_QUOTE' in old_closed.rejection_codes
