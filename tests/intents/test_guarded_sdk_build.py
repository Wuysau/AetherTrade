import pytest


def test_unmeasured_guarded_build_fails_closed():
    from trading_system.engine.nautilus.guarded_sdk_build import inspect_guarded_sdk
    with pytest.raises(ValueError, match='GUARDED_SDK_BUILD_NOT_QUALIFIED'):
        inspect_guarded_sdk()


def test_guarded_factory_has_no_transport_or_callback_parameters():
    from inspect import signature
    from trading_system.engine.nautilus.guarded_creation import submit_guarded_creation_probe
    assert tuple(signature(submit_guarded_creation_probe).parameters) == (
        'engine','write','request','permit','boundary','lease','owner')


def test_guarded_factory_rejects_untyped_caller_before_io():
    from trading_system.engine.nautilus.guarded_creation import submit_guarded_creation_probe
    with pytest.raises(ValueError, match='NATIVE_ACTUAL_OWNER_ENGINE_REQUIRED'):
        submit_guarded_creation_probe(None,None,None,None,boundary=None,lease=None,owner=None)
