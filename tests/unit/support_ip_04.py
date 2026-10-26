"""Synthetic local inputs only; these are not venue/qualification evidence."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal

from trading_system.accounts.authorization import AuthorizationBinding, AuthorizationService
from trading_system.accounts.capabilities import CapabilityCatalog
from trading_system.accounts.identity import IdentityCatalog
from trading_system.contracts import types as t

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
START = '2026-10-22T11:00:00Z'
END = '2026-10-22T13:00:00Z'


@dataclass
class Scenario:
    scope: t.ScopeKey
    identity: t.AccountIdentityEvidence
    grant: AuthorizationBinding
    context: t.DecisionContext
    query: t.CapabilityQuery
    grants: dict[str, AuthorizationBinding]
    identities: IdentityCatalog
    capabilities: CapabilityCatalog
    authorizations: AuthorizationService
    verified: set[tuple[str, t.AccountIdentityEvidence]]


def scenario(environment=t.Environment.PAPER):
    scope = t.ScopeKey(environment=environment, venue_account_id='venue-account-1',
                       product=t.Product.SPOT, instrument_uid='binance:spot:BTCUSDT', venue_leg='BOTH')
    ref = t.EvidenceRef(id='synthetic-proof', hash='a' * 64, environment=environment,
                        source='offline-fixture', visible_at=START)
    identity = t.AccountIdentityEvidence(venue_account_id=scope.venue_account_id,
                                         venue='BINANCE', product=scope.product,
                                         environment=environment, evidence=ref)
    authorization = t.Authorization(
        authorization_id='auth-1', owner_strategy_id='strategy-1', environment=environment,
        deployment_id='deployment-1', scopes=(scope,),
        maximum_capital=t.Money(amount=Decimal('100'), currency='USDT'), policy_revision=1,
        protection_options=('NATIVE_REQUIRED',),
        model_cost_budget=t.Money(amount=Decimal('0'), currency='USD'),
        valid_from=START, expires_at=END, revoked_at=None)
    grant = AuthorizationBinding(authorization=authorization, identity_id='actor-1',
                                 strategy_revision=1, ownership_epoch=1,
                                 queue_environment=environment, capabilities=('read', 'trade'), evidence=(ref,))
    context = t.DecisionContext(identity_id='actor-1', environment=environment,
                                deployment_id='deployment-1', authorization_id='auth-1',
                                policy_revision=1, ownership_epoch=1, scopes=(scope,),
                                strategy_revision=1, decision_run_id='run-1',
                                snapshot_ref=t.SnapshotRef(id='snapshot-1', hash='b' * 64,
                                                          environment=environment, source='fixture', visible_at=START))
    profile = t.CapabilityProfile(scope=scope, build_digest='locked-build',
                                  status=t.CapabilityStatus.VERIFIED, capabilities=('read', 'trade'),
                                  limitations=(), evidence=(ref,), valid_from=START, valid_until=END)
    query = t.CapabilityQuery(scope=scope, requested=('trade',), platform=profile,
                              adapter=profile, account=profile, authorization=authorization)
    grants = {'auth-1': grant}
    verified = {('key_a', identity), ('key_b', identity)}
    clock = lambda: NOW
    return Scenario(scope, identity, grant, context, query, grants,
                    IdentityCatalog(environment, verify=lambda key, proof: (key, proof) in verified, clock=clock),
                    CapabilityCatalog('locked-build', grants.get, clock=clock),
                    AuthorizationService(grants.get, clock=clock), verified)


def change_grant(s, **changes):
    s.grants['auth-1'] = replace(s.grant, **changes)
