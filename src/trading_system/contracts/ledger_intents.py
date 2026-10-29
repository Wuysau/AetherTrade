"""Explicit immutable ledger intent registry, never a legacy execution adapter."""
from dataclasses import dataclass

from . import types as t
from .wire import ContractCodec, TradeIntent
from .program_financial import ProgramFinancialIntent


class LedgerIntentRecord:
    """Static decoder selector; never a persisted record or model class name."""


class LedgerIntentCodec:
    def decode(self,payload):
        if not isinstance(payload,dict):raise ValueError('LEDGER_INTENT_OBJECT_REQUIRED')
        if payload.get('contract_version')=='2026-10-20' and 'record_kind' not in payload:
            return ContractCodec().decode('trade-intent',payload)
        if payload.get('contract_version')=='2026-10-29' and payload.get('record_kind')=='PROGRAM_FINANCIAL_INTENT':
            from .order_codec import VersionedOrderCodec
            return VersionedOrderCodec().decode('program-financial-intent',payload)
        raise ValueError('LEDGER_INTENT_KIND_OR_VERSION_UNSUPPORTED')

    def encode(self,intent):
        if type(intent) is TradeIntent:
            return ContractCodec().encode(intent)
        if type(intent) is ProgramFinancialIntent:
            from .order_codec import VersionedOrderCodec
            return VersionedOrderCodec().encode('program-financial-intent',intent)
        raise ValueError('LEDGER_INTENT_TYPE_UNSUPPORTED')

    def hash(self,intent):
        from .canonical import canonical_hash
        return canonical_hash(self.encode(intent))


@dataclass(frozen=True,kw_only=True)
class IntentIdentity:
    intent_id: str
    scope: t.ScopeKey
    collateral_group_id: str
    owner_strategy_id: str
    ownership_epoch: int
    policy_revision: int


def intent_identity(intent):
    LedgerIntentCodec().encode(intent)
    scope=(t.ScopeKey(environment=t.Environment(intent.environment),venue_account_id=intent.venue_account_id,
        product=t.Product(intent.product),instrument_uid=intent.instrument_uid,venue_leg=intent.venue_leg)
        if type(intent) is TradeIntent else intent.scope)
    return IntentIdentity(intent_id=intent.intent_id,scope=scope,collateral_group_id=intent.collateral_group_id,
        owner_strategy_id=intent.owner_strategy_id,ownership_epoch=intent.ownership_epoch,
        policy_revision=intent.risk_policy_revision)
