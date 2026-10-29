"""Explicit new-version dispatch; legacy records are decoded, never upgraded."""
from jsonschema import Draft202012Validator,FormatChecker
import json
from .canonical import canonical_json,canonical_hash,domain_payload
from .resources import contract_path
from .wire import ContractCodec
from .order_program import OrderProgram,AtomicOrderSpec,OrderProposal,OrderProgramCodec
from .order_lifecycle import ParentAuthority,ChildObservation,ProgramState,ApplicationWrite
from .order_snapshots import OrderCapabilitySnapshot,OrderRuleSnapshot

KINDS={'order-proposal':OrderProposal,'order-program':OrderProgram,'atomic-order':AtomicOrderSpec,'parent-authority':ParentAuthority,
       'child-observation':ChildObservation,'program-state':ProgramState,'application-write':ApplicationWrite,
       'order-capability-snapshot':OrderCapabilitySnapshot,'order-rule-snapshot':OrderRuleSnapshot}
LEGACY={'decision-proposal','trade-intent','execution-command','risk-policy'}

class VersionedOrderCodec:
    def decode(self,kind,payload,*,version=None):
        from trading_system.storage.models import decode
        version=version or payload.get('contract_version')
        if version=='2026-10-20' and kind in LEGACY:return ContractCodec().decode(kind,payload)
        if version!='2026-10-28' or kind not in KINDS:raise ValueError('Unknown contract version/kind; never recompile historical orders')
        text=canonical_json(payload)
        if len(text.encode())>1048576:raise ValueError('Order record too large')
        schema=json.loads(contract_path(kind+'.v2026-10-28.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema,format_checker=FormatChecker()).validate(payload)
        return decode(KINDS[kind],payload)
    def encode(self,kind,record):
        if kind in LEGACY:return ContractCodec().encode(record)
        if kind not in KINDS or type(record) is not KINDS[kind]:raise ValueError('Record kind mismatch')
        payload=domain_payload(record);self.decode(kind,payload,version='2026-10-28');return payload
    def hash(self,kind,record):return canonical_hash(self.encode(kind,record))
