"""Explicit new-version dispatch; legacy records are decoded, never upgraded."""
from jsonschema import Draft202012Validator,FormatChecker
import json
from .canonical import canonical_json,canonical_hash,domain_payload
from .resources import contract_path
from .wire import ContractCodec
from .order_program import OrderProgram,AtomicOrderSpec,OrderProposal,OrderProgramCodec
from .order_lifecycle import ParentAuthority,ChildObservation,ProgramState,ApplicationWrite,ParentObservation
from .order_program_v3 import OrderProgramV3,OrderProposalV3
from .order_lifecycle_v3 import (ParentAuthorityV3,ParentBindingV3,ChildMembershipV3,NativeExclusivityV3,
    ChildObservationV3,ParentObservationV3,ReceiptAllocationV3,LifecycleProofV3,ProgramStateV3)
from .order_snapshots import OrderCapabilitySnapshot,OrderRuleSnapshot
from .program_financial import ProgramFinancialIntent,ProgramFinancialRiskBasis
from .application_write_v3 import ApplicationWriteV3

KINDS={'order-proposal':OrderProposal,'order-program':OrderProgram,'atomic-order':AtomicOrderSpec,'parent-authority':ParentAuthority,'parent-observation':ParentObservation,
       'child-observation':ChildObservation,'program-state':ProgramState,'application-write':ApplicationWrite,
       'order-capability-snapshot':OrderCapabilitySnapshot,'order-rule-snapshot':OrderRuleSnapshot}
KINDS_BY_VERSION={'2026-10-28':KINDS,'2026-10-29':{
    'order-program':OrderProgramV3,'order-proposal':OrderProposalV3,
    'program-financial-intent':ProgramFinancialIntent,'program-financial-risk-basis':ProgramFinancialRiskBasis,
    'application-write':ApplicationWriteV3,
    'parent-authority':ParentAuthorityV3,'parent-binding':ParentBindingV3,
    'child-membership':ChildMembershipV3,'native-exclusivity':NativeExclusivityV3,
    'child-observation':ChildObservationV3,'parent-observation':ParentObservationV3,
    'receipt-allocation':ReceiptAllocationV3,'lifecycle-proof':LifecycleProofV3,'program-state':ProgramStateV3}}

LEGACY={'decision-proposal','trade-intent','execution-command','risk-policy'}

class VersionedOrderCodec:
    def decode(self,kind,payload,*,version=None):
        from trading_system.storage.models import decode
        version=version or payload.get('contract_version')
        if version=='2026-10-20' and kind in LEGACY:return ContractCodec().decode(kind,payload)
        kinds=KINDS_BY_VERSION.get(version,{})
        if kind not in kinds:raise ValueError('Unknown contract version/kind; never recompile historical orders')
        text=canonical_json(payload)
        if len(text.encode())>1048576:raise ValueError('Order record too large')
        schema=json.loads(contract_path(kind+'.v'+version+'.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema,format_checker=FormatChecker()).validate(payload)
        return decode(kinds[kind],payload)
    def encode(self,kind,record):
        if kind in LEGACY:return ContractCodec().encode(record)
        version=getattr(record,'contract_version','2026-10-28')
        kinds=KINDS_BY_VERSION.get(version,{})
        if kind not in kinds or type(record) is not kinds[kind]:raise ValueError('Record kind mismatch')
        payload=domain_payload(record);self.decode(kind,payload,version=version);return payload
    def hash(self,kind,record):return canonical_hash(self.encode(kind,record))
