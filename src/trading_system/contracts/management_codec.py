"""Closed V4 route, deliberately absent from the shared creation registry."""
import json
from jsonschema import Draft202012Validator
from .canonical import canonical_json,domain_payload
from .management_write_v4 import ManagementWriteV4
from .resources import contract_path


class ManagementCodec:
    def __init__(self):
        self.validator=Draft202012Validator(json.loads(contract_path('management-write.v2026-10-30-v4.schema.json').read_text(encoding='utf-8')))

    def encode(self,record):
        if type(record) is not ManagementWriteV4:raise ValueError('MANAGEMENT_V4_REQUIRED')
        payload=domain_payload(record);self.validator.validate(payload)
        return canonical_json(payload)

    def decode(self,payload):
        from trading_system.storage.models import decode
        if type(payload) is str:payload=json.loads(payload)
        self.validator.validate(payload)
        record=decode(ManagementWriteV4,payload)
        if canonical_json(payload)!=self.encode(record):raise ValueError('MANAGEMENT_TYPE_CHANGED')
        return record
