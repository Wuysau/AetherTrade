"""Malformed binding/history cases; real SQL grouping is tested separately."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from trading_system.contracts import types as t
from trading_system.storage.models import MissingEvidence, account_key, encode, scope_id
from trading_system.storage.uow import RiskLockRepository
from unit.test_locked_actions import LockedCase


class ApplicabilityCase:
    def __init__(self):
        self.case = LockedCase()
        self.source = replace(self.case.scope, instrument_uid='BTC')
        self.target = replace(self.case.scope, instrument_uid='ETH')
        self.lock = replace(self.case.locks[0], scope=self.source, required_evidence=('DAILY_LOSS_REVIEW',))
        self.rows = [(scope_id(self.source), self.lock.lock_id, 1, self.lock)]
        self.histories = {(scope_id(self.source), self.lock.lock_id): [(1, self.lock)]}
        self.bindings = {account_key(self.source): [('group',)]}
        self.mutex = [('group',)]
        self.tx = SimpleNamespace(scope=self.target, group_id='group', execute=self.execute,
            require_scope=lambda scope: None)
        self.repo = RiskLockRepository(self.tx)

    def execute(self, sql, params):
        if 'FROM ledger.account_bindings' in sql:
            rows = self.bindings.get(params, [])
        elif 'FROM ledger.collateral_mutex' in sql:
            rows = self.mutex
        elif 'SELECT DISTINCT ON' in sql:
            rows = self.rows
        else:
            rows = self.histories[params]
        serialized = [tuple(encode(value) if isinstance(value, t.RiskLock) else value
                            for value in row) for row in rows]
        return SimpleNamespace(fetchall=lambda: serialized)


def test_original_source_identity_is_retained_for_matching_group():
    case = ApplicabilityCase()
    assert case.repo.applicable(case.target) == (case.lock,)
    assert case.lock.scope == case.source


@pytest.mark.parametrize('fault', ['current_missing', 'current_group', 'current_ambiguous',
    'mutex_missing', 'source_missing', 'source_ambiguous', 'weakened_permission', 'missing_review',
    'scope_identity', 'record_identity', 'changed_history', 'duplicate_identity'])
def test_unverified_applicability_never_returns_an_empty_safe_view(fault):
    case = ApplicabilityCase()
    source_key = account_key(case.source)
    if fault.startswith('source_'):
        case.source = replace(case.source, venue_account_id='separate-source-account')
        case.lock = replace(case.lock, scope=case.source)
        case.rows = [(scope_id(case.source), case.lock.lock_id, 1, case.lock)]
        case.histories = {(scope_id(case.source), case.lock.lock_id): [(1, case.lock)]}
        source_key = account_key(case.source)
    if fault == 'current_missing':
        case.bindings.clear()
    elif fault == 'current_group':
        case.bindings[account_key(case.target)] = [('changed-group',)]
    elif fault == 'current_ambiguous':
        case.bindings[account_key(case.target)] = [('group',), ('group',)]
    elif fault == 'mutex_missing':
        case.mutex = []
    elif fault == 'source_missing':
        case.bindings[source_key] = []
    elif fault == 'source_ambiguous':
        case.bindings[source_key] = [('group',), ('other',)]
    elif fault in ('weakened_permission', 'missing_review'):
        case.lock = replace(case.lock, **({'permission': t.RiskPermission.NORMAL}
            if fault == 'weakened_permission' else {'required_evidence': ('arbitrary',)}))
        case.rows = [(scope_id(case.source), case.lock.lock_id, 1, case.lock)]
        case.histories[(scope_id(case.source), case.lock.lock_id)] = [(1, case.lock)]
    elif fault == 'scope_identity':
        case.rows = [('invalid-scope-id', case.lock.lock_id, 1, case.lock)]
    elif fault == 'record_identity':
        case.rows = [(scope_id(case.source), 'invalid-record-id', 1, case.lock)]
    elif fault == 'changed_history':
        changed = replace(case.lock, cause='MODEL_HEALTH')
        case.rows = [(scope_id(case.source), case.lock.lock_id, 2, changed)]
        case.histories[(scope_id(case.source), case.lock.lock_id)] = [(1, case.lock), (2, changed)]
    elif fault == 'duplicate_identity':
        duplicate = replace(case.lock, scope=case.target)
        case.rows.append((scope_id(case.target), duplicate.lock_id, 1, duplicate))
        case.histories[(scope_id(case.target), duplicate.lock_id)] = [(1, duplicate)]
    with pytest.raises(MissingEvidence):
        case.repo.applicable(case.target)


def test_position_cannot_assert_unverified_foreign_daily_scope():
    case = LockedCase()
    foreign = replace(case.locks[0], scope=replace(case.scope, instrument_uid='foreign'))
    case.locks = ()
    case.book = replace(case.book, positions=(replace(case.book.positions[0], risk_locks=(foreign,)),))
    from trading_system.execution.dispatcher import DispatchDenied
    with pytest.raises(DispatchDenied):
        case.check()
