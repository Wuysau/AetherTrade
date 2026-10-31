"""Neutral group duties for serial integration; never releases or grants cash."""
from dataclasses import dataclass,replace
from trading_system.contracts.management_codec import ManagementCodec
from trading_system.contracts.management_write_v4 import ManagementWriteV4
from trading_system.storage.models import account_key,decode,scope_id,MissingEvidence
from .program_management_commands import ManagementBinding,binding_hash,digest

@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementObligation:
    original:ManagementWriteV4
    current:ManagementWriteV4
    collateral_group_id:str
    aborted_unsent:bool=False

    @property
    def unresolved(self):
        # Even a source-confirmed cancellation does not reconcile late fills,
        # protection, remaining parent children or retained collateral.
        return not self.aborted_unsent

    @property
    def identity(self):return 'MANAGEMENT_V4:'+scope_id(self.original.scope)+':'+self.original.command_id

def management_obligations(tx,*,scope_only=False):
    if tx.execute("SELECT to_regclass('ledger.management_bindings')").fetchone()[0] is None:return ()
    rows=tx.execute('SELECT scope_id,record_id,payload FROM ledger.management_bindings ORDER BY scope_id,record_id').fetchall()
    result=[];codec=ManagementCodec()
    try:
        for physical,cid,raw in rows:
            b=decode(ManagementBinding,raw);w=b.write;codec.encode(w)
            if physical!=scope_id(w.scope) or cid!=w.command_id or w.state!='DURABLE_READY' or binding_hash(b)!=w.binding_hash:
                raise ValueError('original binding changed')
            if w.scope.environment!=tx.scope.environment:continue
            group=tx.execute('SELECT collateral_group_id FROM ledger.account_bindings WHERE environment=%s AND account_id=%s AND product=%s',account_key(w.scope)).fetchone()
            if group is None:raise ValueError('collateral binding missing')
            if group[0]!=tx.group_id:continue
            history=tx.execute('SELECT sequence,payload FROM ledger.management_writes WHERE scope_id=%s AND record_id=%s ORDER BY sequence',(physical,cid)).fetchall()
            if not 1<=len(history)<=3 or tuple(n for n,_ in history)!=tuple(range(1,len(history)+1)):
                raise ValueError('history missing')
            writes=tuple(codec.decode(p) for _,p in history)
            if tuple(v.state for v in writes)!=('DURABLE_READY','MAY_HAVE_SENT','UNKNOWN')[:len(writes)] or any(
                    digest(replace(v,state='DURABLE_READY'))!=digest(w) for v in writes):raise ValueError('history changed')
            original=tx.execute('SELECT payload FROM ledger.order_writes_v2 WHERE scope_id=%s AND record_id=%s AND sequence=1',
                (physical,w.original_command_id)).fetchone()
            if original is None or digest(original[0])!=b.original_write_hash:raise ValueError('original creation changed')
            if len(writes)>1 and tx.execute('SELECT count(*) FROM ledger.management_dispatch_marks WHERE scope_id=%s AND command_id=%s',
                    (physical,cid)).fetchone()!=(1,):raise ValueError('protected MAY missing')
            aborted=tx.execute('SELECT payload FROM ledger.management_unsent_aborts WHERE scope_id=%s AND command_id=%s',
                (physical,cid)).fetchone()
            if aborted is not None:
                p=aborted[0]
                if len(writes)!=1 or p!={'command_id':cid,'binding_hash':w.binding_hash,'state':'ABORTED_UNSENT','funds_released':False}:
                    raise ValueError('unsent abort proof changed')
            if not scope_only or w.scope==tx.scope:result.append(ManagementObligation(original=w,current=writes[-1],
                collateral_group_id=group[0],aborted_unsent=aborted is not None))
    except Exception as exc:
        raise MissingEvidence('MANAGEMENT_OBLIGATION_SOURCE_INVALID') from exc
    return tuple(result)
