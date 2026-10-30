"""Account-wide authoritative asset contributions, not a running balance ledger.

Read-only instrument views share the actual collateral transaction and cannot
write another instrument. All approvals are checked, each native execution is
counted once, and every owner posting is rederived against protected source and
the existing FillLedger. Missing data is not an empty contribution.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,decimal_string
from trading_system.contracts.wire import ExecutionCommand
from trading_system.storage.models import decode,scope_id,MissingEvidence,ScopeMismatch
from .fills import FillLedger
from .program_account_opening import ProgramAccountKey,account_key,read_program_account_opening
from .program_asset_reader import ProgramAssetPostingReader
from .program_asset_postings import ProgramAssetPostingRecord,make_program_asset_posting,economic_signature


def need(condition,code):
    if not condition:raise MissingEvidence(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramAccountCash:
    account:ProgramAccountKey
    opening_id:str
    economic_revision:str
    balances:tuple[t.Money,...]
    native_identities:tuple[str,...]
    legacy_fill_ids:tuple[str,...]
    retained_duties:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


class _AccountScopeRead:
    """Internal SELECT-only view, not a second UoW or a scope write bypass."""
    def __init__(self,tx,scope):
        need(account_key(scope)==account_key(tx.scope),'ACCOUNT_SOURCE_SCOPE_CHANGED')
        self._tx,self.scope=tx,scope
    @property
    def active(self):return self._tx.active
    @property
    def failed(self):return self._tx.failed
    @property
    def group_id(self):return self._tx.group_id
    @property
    def transaction_id(self):return self._tx.transaction_id
    def require_scope(self,scope):
        if scope!=self.scope:raise ScopeMismatch('account source read scope mismatch')
    def execute(self,sql,params=()):
        if not sql.lstrip().startswith('SELECT ') or ';' in sql:raise ValueError('account source view is read-only')
        return self._tx.execute(sql,params)


def _native_key(fill):
    return (scope_id(fill.scope),fill.venue,fill.native_trade_id)


def _legacy_effects(tx,fill,raw_commands):
    commands=tuple(decode(ExecutionCommand,p) for p in raw_commands)
    candidates=tuple(c for c in commands if c.operation in ('SUBMIT','CLOSE','SET_PROTECTION'))
    need(len(candidates)==1,'AMBIGUOUS_LEGACY_NATIVE_ORIGIN')
    command=candidates[0]
    need((command.environment,command.venue_account_id,command.product,command.instrument_uid)==(
        fill.scope.environment,fill.scope.venue_account_id,fill.scope.product,fill.scope.instrument_uid),
        'LEGACY_NATIVE_ORIGIN_SCOPE_CHANGED')
    need(fill.scope.product==t.Product.SPOT and fill.quantity.unit=='BASE' and bool(fill.fees),
        'LEGACY_SETTLEMENT_OR_FEES_UNVERIFIED')
    side=command.canonical_payload.get('side');symbol=command.canonical_payload.get('symbol')
    if symbol is None:
        row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
            (scope_id(fill.scope),'console:request:'+command.command_id)).fetchone()
        symbol=None if row is None else row[0].get('symbol')
    quote=fill.price.currency
    need(side in ('BUY','SELL') and type(symbol) is str and bool(quote) and symbol.endswith(quote)
        and len(symbol)>len(quote),'LEGACY_ASSET_METADATA_REQUIRED')
    base=symbol[:-len(quote)];sign=Decimal(1) if side=='BUY' else Decimal(-1)
    need(all(m.amount.is_finite() and m.currency for m in fill.fees),'LEGACY_FEE_HISTORY_UNVERIFIED')
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        deltas={base:sign*fill.quantity.value,quote:-sign*fill.quantity.value*fill.price.value}
        for fee in fill.fees:deltas[fee.currency]=deltas.get(fee.currency,Decimal(0))-fee.amount
    signature=canonical_hash(dict(fill=domain_payload(fill),origin=domain_payload(command)))
    return deltas,signature


def read_program_account_cash(tx,*,read_opening=None):
    """Return None only for the legacy/no-generic branch, never unknown cash.

    read_opening is an explicitly registered local provenance reader. The
    supported initial reader always rereads its protected SQL row; arbitrary
    caller functions or opening DTOs are not accepted as capital authority.
    """
    source_table=tx.execute("SELECT to_regclass('ledger.program_fill_source_approvals')").fetchone()[0]
    if source_table is None:return None
    account=account_key(tx.scope)
    sources=tx.execute('SELECT scope_id,approval_id,identity_hash,payload FROM ledger.program_fill_source_approvals '
        "WHERE payload#>>'{scope,environment}'=%s AND payload#>>'{scope,venue_account_id}'=%s "
        "AND payload#>>'{scope,product}'=%s ORDER BY scope_id,approval_id",
        (str(account.environment),account.venue_account_id,str(account.product))).fetchall()
    rows=tx.execute('SELECT f.payload FROM ledger.fills f WHERE f.environment=%s AND f.account_id=%s AND f.product=%s',
        (str(account.environment),account.venue_account_id,str(account.product))).fetchall()
    fills=tuple(decode(t.Fill,row[0]) for row in rows)
    origins={}
    for fill in fills:
        legacy=tx.execute('SELECT payload FROM ledger.commands WHERE scope_id=%s AND sequence=1 '
            "AND payload->>'logical_client_order_id'=%s",(scope_id(fill.scope),fill.order.logical_client_order_id)).fetchall()
        origins[_native_key(fill)]=tuple(row[0] for row in legacy)
    generic_fills=tuple(f for f in fills if not origins[_native_key(f)])
    if read_opening is None:
        need(not sources and not generic_fills,'ACCOUNT_OPENING_REQUIRED')
        return None
    from .program_legacy_opening import read_program_legacy_opening,read_program_local_account_opening
    need(any(read_opening is reader for reader in (read_program_account_opening,read_program_legacy_opening,read_program_local_account_opening)),
        'TRUSTED_ACCOUNT_OPENING_READER_REQUIRED')
    opening=read_opening(tx)
    if opening is None:
        need(not sources and not generic_fills,'ACCOUNT_OPENING_REQUIRED')
        return None
    need(opening.account==account,'ACCOUNT_OPENING_BINDING_CHANGED')
    # The historical command table and owner-written fill/audit rows are not a
    # source attestation. No authenticated legacy fee/history adapter is yet
    # registered. Preserve that history but block mixed credit until an actual
    # protected legacy source contract is available; never infer .001 fees.
    need(not any(origins[_native_key(f)] for f in fills),'LEGACY_AUTHENTICATED_SOURCE_REQUIRED')
    readers={};facts={};covered=set();duties=[]
    for stored_scope,approval_id,identity,payload in sources:
        scope=decode(t.ScopeKey,payload['scope'])
        need(stored_scope==scope_id(scope),'ACCOUNT_SOURCE_SCOPE_CHANGED')
        if stored_scope not in readers:
            view=_AccountScopeRead(tx,scope)
            readers[stored_scope]=ProgramAssetPostingReader(view,fill_ledger=FillLedger(None,scope,trade_id_scope='INSTRUMENT'))
        reader=readers[stored_scope];fact=reader.read(approval_id)
        need(fact.approval.identity_hash==identity,'ACCOUNT_NATIVE_IDENTITY_CHANGED')
        signature=economic_signature(fact)
        if identity in facts:
            need(economic_signature(facts[identity])==signature,'ACCOUNT_NATIVE_ECONOMICS_CONFLICT')
        else:
            marker=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
                (stored_scope,'program-asset-posting:'+identity)).fetchone()
            need(marker is not None,'UNPOSTED_PROGRAM_ASSET_EFFECTS')
            try:
                record=decode(ProgramAssetPostingRecord,marker[0])
                original=reader.read(record.source_approval_id)
                need(record==make_program_asset_posting(original) and economic_signature(original)==signature,
                    'PROGRAM_ASSET_POSTING_MISMATCH')
            except (ValueError,TypeError,KeyError) as error:
                raise MissingEvidence('PROGRAM_ASSET_POSTING_MISMATCH') from error
            facts[identity]=fact
        covered.add(_native_key(fact.fill));duties.extend(fact.approval.retained_duties)
    need({_native_key(f) for f in generic_fills}==covered,'UNAPPROVED_ACCOUNT_FILL_HISTORY')
    vector={m.currency:m.amount for m in opening.balances};legacy_signatures=[]
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        for fill in fills:
            if not origins[_native_key(fill)]:continue
            delta,signature=_legacy_effects(tx,fill,origins[_native_key(fill)])
            legacy_signatures.append((fill.fill_id,signature))
            for asset,amount in delta.items():vector[asset]=vector.get(asset,Decimal(0))+amount
        for identity in sorted(facts):
            for effect in facts[identity].effects.asset_deltas:
                vector[effect.currency]=vector.get(effect.currency,Decimal(0))+effect.amount
    need(all(value>=0 for value in vector.values()),'NEGATIVE_RECONSTRUCTED_ASSET_BALANCE')
    revision=canonical_hash(dict(account=domain_payload(account),opening_id=opening.opening_id,
        legacy=[list(v) for v in sorted(legacy_signatures)],
        program=[dict(identity=k,economics=economic_signature(facts[k])) for k in sorted(facts)]))
    return ProgramAccountCash(account=account,opening_id=opening.opening_id,economic_revision=revision,
        balances=tuple(t.Money(currency=asset,amount=value) for asset,value in sorted(vector.items())),
        native_identities=tuple(sorted(facts)),legacy_fill_ids=tuple(k for k,_ in sorted(legacy_signatures)),
        retained_duties=tuple(dict.fromkeys(duties)))
