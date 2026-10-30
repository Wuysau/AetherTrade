"""Separate patched artifact registry. Original sdk_pins stays byte-exact."""
from hashlib import sha256
import importlib.metadata
from pathlib import Path
import platform
import sys

SOURCE_COMMIT = '27a8e54e7ac3c57d6cbf8891f0283dfbaee97317'
# Populated only after full wheel + real quota/transport + durable PG qualification.
# A caller-provided descriptor cannot grant trust to arbitrary native code.
QUALIFIED_BUILD = None


def inspect_guarded_sdk():
    measured = QUALIFIED_BUILD
    if measured is None:
        raise ValueError('GUARDED_SDK_BUILD_NOT_QUALIFIED')
    import nautilus_trader.core.nautilus_pyo3 as native
    if (measured['source_commit'] != SOURCE_COMMIT
            or measured['version'] != importlib.metadata.version('nautilus_trader')
            or measured['python'] != platform.python_version()
            or measured['platform'] != sys.platform
            or measured['machine'] != platform.machine()
            or measured['module_sha256'] != sha256(Path(native.__file__).read_bytes()).hexdigest()
            or not hasattr(native.HttpClient, 'request_guarded')):
        raise ValueError('GUARDED_SDK_MEASUREMENT_CHANGED')
    return dict(measured)
