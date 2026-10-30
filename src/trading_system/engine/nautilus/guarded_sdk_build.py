"""Separate patched artifact registry. Original sdk_pins stays byte-exact."""
from hashlib import sha256
import importlib.metadata
from pathlib import Path
import platform
import sys
import sysconfig

SOURCE_COMMIT = '27a8e54e7ac3c57d6cbf8891f0283dfbaee97317'
# Enrollment requires full wheel + real quota/transport + durable PG evidence.
# Metadata supplied by a caller never grants trust to native code.
QUALIFIED_BUILD = None
_MODULE_CACHE = None


def _module_digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def measure_loaded_guarded_sdk():
    """Read-only startup measurement, before MAY; never enroll or renew a build."""
    global _MODULE_CACHE
    import nautilus_trader.core.nautilus_pyo3 as native
    path = Path(native.__file__).resolve()
    stat = path.stat()
    guard = getattr(native.HttpClient, 'request_guarded', None)
    version = importlib.metadata.version('nautilus_trader')
    key = (str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,
           native.HttpClient,guard,version)
    if _MODULE_CACHE is None or _MODULE_CACHE[0] != key:
        result = dict(version=version,python=platform.python_version(),platform=sys.platform,
            implementation=sys.implementation.name,machine=platform.machine(),
            abi=sysconfig.get_config_var('SOABI'),libc=platform.libc_ver(),
            module_sha256=_module_digest(path),
            guard_signature=getattr(guard,'__text_signature__','') or '',
            execution_authorized=False,external_activation_allowed=False)
        _MODULE_CACHE = key,result
    return dict(_MODULE_CACHE[1])


def inspect_guarded_sdk():
    """Closed registry check; trusted host preloads this before issuing permits."""
    expected = QUALIFIED_BUILD
    if expected is None:
        raise ValueError('GUARDED_SDK_BUILD_NOT_QUALIFIED')
    try:
        actual = measure_loaded_guarded_sdk()
    except ImportError as exc:
        raise ValueError('GUARDED_SDK_BUILD_NOT_QUALIFIED') from exc
    if (expected['source_commit'] != SOURCE_COMMIT or actual['implementation'] != 'cpython'
            or any(expected[key] != actual[key] for key in
                   ('version','python','platform','machine','abi','module_sha256'))
            or tuple(expected['runtime_libc']) != actual['libc']
            or 'budget_ms' not in actual['guard_signature']):
        raise ValueError('GUARDED_SDK_MEASUREMENT_CHANGED')
    return dict(expected)
