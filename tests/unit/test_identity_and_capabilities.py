"""Offline business assertions; none register or pass T01-T66 qualification."""
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_system.accounts.identity import IdentityCatalog, IdentityVerificationError
from trading_system.contracts import types as t
from unit.support_ip_04 import END, NOW, START, change_grant, scenario


def test_keys_alias_same_account():
    s = scenario()
    a = s.identities.bind('key_a', s.identity)
    b = s.identities.bind('key_b', s.identity)
    assert a.venue_account_id == b.venue_account_id
    assert {r.id for r in b.credential_refs} == {'key_a', 'key_b'}
    assert b.products == (t.Product.SPOT,)
    assert s.identities.bind('key_b', s.identity) == b
    # Budget/owner keys stay identical; this catalog allocates no funds.
    assert replace(s.scope, venue_account_id=a.venue_account_id) == replace(s.scope, venue_account_id=b.venue_account_id)


def test_key_rotation_requires_permission_and_identity_reverification():
    s = scenario()
    old = s.identities.bind('key_a', s.identity)
    with pytest.raises(IdentityVerificationError):
        s.identities.bind('new_key', s.identity)
    assert s.identities.status('new_key') == t.CapabilityStatus.UNVERIFIED
    s.verified.add(('new_key', s.identity))
    new = s.identities.bind('new_key', s.identity)
    assert old.venue_account_id == new.venue_account_id
    s.verified.remove(('new_key', s.identity))
    with pytest.raises(IdentityVerificationError):
        s.identities.bind('new_key', s.identity)
    assert s.identities.status('new_key') == t.CapabilityStatus.UNVERIFIED


@pytest.mark.parametrize('environment', list(t.Environment))
def test_identity_environment_isolation(environment):
    s = scenario(environment)
    for other in t.Environment:
        if other == environment:
            continue
        proof = scenario(other).identity
        s.verified.add(('key_a', proof))
        with pytest.raises(IdentityVerificationError):
            s.identities.bind('key_a', proof)


@pytest.mark.parametrize('change', [
    {'venue_account_id': ''}, {'venue_account_id': 'key_a'}, {'venue': ''}, {'product': 'invalid'},
])
def test_identity_rejects_invalid_fields(change):
    s = scenario()
    proof = replace(s.identity, **change)
    s.verified.add(('key_a', proof))
    with pytest.raises(IdentityVerificationError):
        s.identities.bind('key_a', proof)
    assert s.identities.status('key_a') == t.CapabilityStatus.UNVERIFIED


def test_identity_never_remaps_an_existing_key_or_collides_venues():
    s = scenario()
    s.identities.bind('key_a', s.identity)
    for key, proof in [('key_a', replace(s.identity, venue_account_id='other')),
                       ('key_b', replace(s.identity, venue='OTHER'))]:
        s.verified.add((key, proof))
        with pytest.raises(IdentityVerificationError):
            s.identities.bind(key, proof)


def test_identity_verifier_failure_is_unverified():
    def unavailable(*args):
        raise OSError('offline')
    catalog = IdentityCatalog(t.Environment.PAPER, verify=unavailable, clock=lambda: NOW)
    with pytest.raises(IdentityVerificationError):
        catalog.bind('key_a', scenario().identity)
    assert catalog.status('key_a') == t.CapabilityStatus.UNVERIFIED


def test_product_uid_strict():
    s = scenario()
    for scope in (replace(s.scope, product=t.Product.USD_M_PERP),
                  replace(s.scope, instrument_uid='binance:perp:BTCUSDT'),
                  replace(s.scope, venue_leg='LONG'), replace(s.scope, venue_account_id='other')):
        result = s.capabilities.intersect(replace(s.query, scope=scope))
        assert not result.allowed
        assert not s.authorizations.validate(replace(s.context, scopes=(scope,))).allowed


@pytest.mark.parametrize('layer', ['platform', 'adapter', 'account', 'user'])
def test_four_way_capability_intersection(layer):
    s = scenario()
    assert s.capabilities.intersect(s.query).allowed == ('trade',)
    if layer == 'user':
        s.grants.clear()
        query = s.query
    else:
        query = replace(s.query, **{layer: replace(getattr(s.query, layer), status=t.CapabilityStatus.UNVERIFIED)})
    result = s.capabilities.intersect(query)
    assert not result.allowed
    assert result.rejection_codes


@pytest.mark.parametrize('layer', ['platform', 'adapter', 'account', 'user'])
def test_unknown_capability_does_not_disable_unrelated_verified_capability(layer):
    s = scenario()
    query = replace(s.query, requested=('read', 'trade', 'read'))
    if layer == 'user':
        change_grant(s, capabilities=('read',))
    else:
        query = replace(query, **{layer: replace(getattr(query, layer), capabilities=('read',))})
    result = s.capabilities.intersect(query)
    assert result.allowed == ('read',)
    assert result.status == t.CapabilityStatus.DEGRADED


@pytest.mark.parametrize('change', [
    {'status': t.CapabilityStatus.DEGRADED}, {'status': t.CapabilityStatus.UNAVAILABLE},
    {'build_digest': 'other'}, {'evidence': ()}, {'valid_until': '2026-10-22T12:00:00Z'},
    {'valid_from': END}, {'valid_from': 'not-a-date'},
])
@pytest.mark.parametrize('layer', ['platform', 'adapter', 'account'])
def test_invalid_profiles_fail_closed(layer, change):
    s = scenario()
    query = replace(s.query, **{layer: replace(getattr(s.query, layer), **change)})
    assert not s.capabilities.intersect(query).allowed


@pytest.mark.parametrize('change', [
    {'environment': t.Environment.LIVE}, {'hash': ''}, {'source': ''},
    {'visible_at': END}, {'visible_at': '2026-10-22T11:00:00'},
])
def test_evidence_environment_provenance_and_visibility(change):
    s = scenario()
    ref = replace(s.identity.evidence, **change)
    proof = replace(s.identity, evidence=ref)
    s.verified.add(('key_a', proof))
    with pytest.raises(IdentityVerificationError):
        s.identities.bind('key_a', proof)
    assert not s.capabilities.intersect(replace(s.query, account=replace(s.query.account, evidence=(ref,)))).allowed
    change_grant(s, evidence=(ref,))
    assert not s.authorizations.validate(s.context).allowed


def test_live_config_requires_new_authorization():
    paper = scenario()
    assert not paper.authorizations.validate(replace(paper.context, environment=t.Environment.LIVE)).allowed
    live = scenario(t.Environment.LIVE)
    # Even a fully populated grant cannot replace native/real-platform qualification.
    assert 'LIVE_NOT_QUALIFIED' in live.authorizations.validate(live.context).rejection_codes
    assert not live.capabilities.intersect(live.query).allowed
    paper.grants.clear()
    assert not paper.authorizations.validate(paper.context).allowed


@pytest.mark.parametrize('environment', [t.Environment.RESEARCH, t.Environment.PAPER, t.Environment.TESTNET])
def test_offline_explicit_authorization(environment):
    s = scenario(environment)
    assert s.authorizations.validate(s.context).allowed
    assert s.capabilities.intersect(s.query).status == t.CapabilityStatus.VERIFIED


@pytest.mark.parametrize('change', [
    {'identity_id': 'other'}, {'deployment_id': 'other'}, {'authorization_id': 'other'},
    {'policy_revision': 2}, {'ownership_epoch': 2}, {'strategy_revision': 2},
    {'scopes': ()}, {'decision_run_id': ''}, {'environment': t.Environment.TESTNET},
])
def test_context_binding_mismatch(change):
    s = scenario()
    assert not s.authorizations.validate(replace(s.context, **change)).allowed


@pytest.mark.parametrize('change', [
    {'revoked_at': START}, {'revoked_at': 'bad'}, {'expires_at': '2026-10-22T12:00:00Z'},
    {'valid_from': END}, {'expires_at': 'bad'}, {'scopes': ()}, {'policy_revision': 0},
    {'owner_strategy_id': ''}, {'protection_options': ()},
    {'maximum_capital': t.Money(amount=Decimal('0'), currency='USD')},
    {'model_cost_budget': t.Money(amount=Decimal('-1'), currency='USD')},
])
def test_invalid_or_revoked_authorizations_are_rechecked(change):
    s = scenario()
    auth = replace(s.grant.authorization, **change)
    change_grant(s, authorization=auth)
    assert not s.authorizations.validate(s.context).allowed
    assert not s.capabilities.intersect(replace(s.query, authorization=auth)).allowed


def test_paper_request_with_live_queue_or_account_is_denied():
    s = scenario()
    change_grant(s, queue_environment=t.Environment.LIVE)
    assert not s.authorizations.validate(s.context).allowed
    assert not s.capabilities.intersect(s.query).allowed
    s = scenario()
    live_scope = replace(s.scope, environment=t.Environment.LIVE)
    auth = replace(s.grant.authorization, scopes=(live_scope,))
    change_grant(s, authorization=auth)
    assert not s.authorizations.validate(replace(s.context, scopes=(live_scope,))).allowed


def test_stale_authorization_query_cannot_reuse_changed_grant():
    s = scenario()
    change_grant(s, authorization=replace(s.grant.authorization, policy_revision=2))
    assert not s.capabilities.intersect(s.query).allowed


def test_empty_capability_request_is_not_a_success():
    s = scenario()
    assert s.capabilities.intersect(replace(s.query, requested=())).status == t.CapabilityStatus.UNAVAILABLE


def test_perpetual_cannot_inherit_cash_hosted_protection():
    s = scenario()
    scope = replace(s.scope, product=t.Product.USD_M_PERP, instrument_uid='binance:perp:BTCUSDT')
    auth = replace(s.grant.authorization, scopes=(scope,), protection_options=('HOSTED_ALLOWED_CASH_ONLY',))
    change_grant(s, authorization=auth)
    decision = s.authorizations.validate(replace(s.context, scopes=(scope,)))
    assert not decision.allowed
    assert 'INVALID_PROTECTION' in decision.rejection_codes


def test_context_snapshot_environment_and_boolean_revisions_are_rejected():
    s = scenario()
    assert not s.authorizations.validate(replace(
        s.context, snapshot_ref=replace(s.context.snapshot_ref, environment=t.Environment.LIVE))).allowed
    assert not s.authorizations.validate(replace(s.context, ownership_epoch=True)).allowed


def test_product_addition_uses_verified_evidence_without_creating_a_new_account():
    s = scenario()
    spot = s.identities.bind('key_a', s.identity)
    proof = replace(s.identity, product=t.Product.USD_M_PERP)
    s.verified.add(('key_b', proof))
    both = s.identities.bind('key_b', proof)
    assert spot.venue_account_id == both.venue_account_id
    assert set(both.products) == {t.Product.SPOT, t.Product.USD_M_PERP}
