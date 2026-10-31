"""Host routing only; domain/database correctness requires the PostgreSQL cohort."""
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from trading_system.runtime.program_paper_host import ProgramPaperHost
from trading_system.execution import program_cost_history
from trading_system.execution.program_legacy_opening import read_program_local_account_opening


@pytest.mark.parametrize('failure',[False,True])
def test_explicit_host_cost_reconciliation_uses_owner_transaction_and_propagates_failure(monkeypatch,failure):
    scope=object();tx=object();proof=object();events=[]
    @contextmanager
    def transaction(actual):
        assert actual is scope
        events.append('begin')
        try:yield tx
        except ValueError:
            events.append('rollback');raise
        else:events.append('commit')
    def reconcile(actual,*,read_opening):
        assert actual is tx and read_opening is read_program_local_account_opening
        events.append('domain')
        if failure:raise ValueError('COST_CURRENT_QUERY_REQUIRED')
        return proof
    monkeypatch.setattr(program_cost_history,'reconcile_program_cost_history',reconcile)
    host=object.__new__(ProgramPaperHost)
    host.uow=SimpleNamespace(transaction=transaction);host.scope=scope
    if failure:
        with pytest.raises(ValueError,match='COST_CURRENT_QUERY_REQUIRED'):host.reconcile_cost_history()
        assert events==['begin','domain','rollback']
    else:
        assert host.reconcile_cost_history() is proof
        assert events==['begin','domain','commit']
