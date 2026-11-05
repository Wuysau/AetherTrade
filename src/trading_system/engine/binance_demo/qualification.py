"""Bounded qualification evidence collector, never an execution grant or signer.

The independently authorized runner must persist reservations before IO and
verify the raw artifacts before signing. This collector performs no IO.
"""
import re
from .contracts import DemoError, decimal


class QualificationRun:
    REQUIRED = frozenset({'PG_FENCING','PG_RECOVERY','ACCOUNT_BASELINE','LIMIT_ACK',
                          'LIMIT_FILL','CANCEL','PRIVATE_STREAM','RECONCILIATION','TAKEOVER'})

    def __init__(self, *, max_commands, max_notional, expires_at):
        if type(max_commands) is not int or not 1 <= max_commands <= 4 or type(expires_at) is not int:
            raise DemoError('DEMO_QUALIFICATION_BUDGET_INVALID')
        self.limit = decimal(max_notional,positive=True)
        if self.limit > 100: raise DemoError('DEMO_QUALIFICATION_BUDGET_INVALID')
        self.max_commands, self.expires_at = max_commands, expires_at
        self.commands, self.results = {}, {}

    def reserve(self, original_id, notional, *, now):
        value = decimal(notional,positive=True)
        if (type(now) is not int or now >= self.expires_at or not isinstance(original_id,str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,36}',original_id) or original_id in self.commands or
                len(self.commands) >= self.max_commands or sum(self.commands.values()) + value > self.limit):
            raise DemoError('DEMO_QUALIFICATION_BUDGET_EXHAUSTED')
        self.commands[original_id] = value

    def record(self, check, state, evidence_sha256):
        if (check not in self.REQUIRED or check in self.results or state not in {'PASS','FAIL','BLOCKED','NOT_RUN'} or
                not isinstance(evidence_sha256,str) or not re.fullmatch('[0-9a-f]{64}',evidence_sha256)):
            raise DemoError('DEMO_QUALIFICATION_RESULT_INVALID')
        self.results[check] = {'state':state,'evidence_sha256':evidence_sha256}

    def summary(self):
        results = {key:dict(self.results.get(key, {'state':'NOT_RUN','evidence_sha256':None})) for key in sorted(self.REQUIRED)}
        states = {item['state'] for item in results.values()}
        state = next((value for value in ('FAIL','BLOCKED','NOT_RUN') if value in states),'PASS')
        return {'state':state,'checks':results,'commands':{k:str(v) for k,v in self.commands.items()},'execution_authorized':False}
