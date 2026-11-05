import time
from types import SimpleNamespace
import pytest
from trading_system.engine.binance_demo.contracts import DemoError
from trading_system.runtime.demo_service import DemoRuntimeService
from trading_system.runtime.demo_control import DemoTakeoverInput


def test_entry_cutoff_blocks_new_risk_without_asserting_flat():
    runtime=SimpleNamespace(entry_deadline=time.monotonic()-1)
    with pytest.raises(DemoError,match='DEMO_ENTRY_WINDOW_ENDED'):
        DemoRuntimeService._require_entry_window(runtime)
    runtime.entry_deadline=time.monotonic()+10
    DemoRuntimeService._require_entry_window(runtime)


def test_takeover_requires_explicit_acknowledgement_and_state_digest():
    value=DemoTakeoverInput(expected_digest='a'*64,acknowledge_responsibility=True)
    assert value.expected_digest=='a'*64
    for fields in ({'expected_digest':'a'*64,'acknowledge_responsibility':False},
                   {'expected_digest':'bad','acknowledge_responsibility':True}):
        with pytest.raises(ValueError): DemoTakeoverInput(**fields)
