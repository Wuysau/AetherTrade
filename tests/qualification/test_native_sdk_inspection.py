"""Measured pinned files are metadata checks, never permission/venue proof."""
from dataclasses import replace

import pytest


def measurements():
    from trading_system.engine.nautilus.sdk_pins import SDK_FILE_SHA256
    return dict(version='1.231.0',python_version='3.14.2',platform_tag='win_amd64',source_hashes=dict(SDK_FILE_SHA256))


def test_pinned_measurements_are_accepted_only_as_metadata():
    from trading_system.engine.nautilus.sdk_pins import validate_sdk_measurements
    result=validate_sdk_measurements(**measurements())
    assert result is None


@pytest.mark.parametrize('fault',['version','python_version','platform_tag','source_hash','missing_source'])
def test_changed_source_version_abi_or_platform_cannot_bind_the_pinned_client(fault):
    from trading_system.engine.nautilus.sdk_pins import validate_sdk_measurements
    values=measurements()
    if fault in ('version','python_version','platform_tag'):values[fault]='other'
    elif fault=='source_hash':values['source_hashes'][next(iter(values['source_hashes']))]='f'*64
    else:values['source_hashes'].pop(next(iter(values['source_hashes'])))
    with pytest.raises(ValueError):validate_sdk_measurements(**values)
