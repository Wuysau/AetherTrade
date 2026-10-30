"""Exact installed SDK inspection; metadata cannot enable an external path."""
from hashlib import sha256
import importlib.metadata
from pathlib import Path
import platform
import sys

SDK_VERSION='1.231.0'
SDK_SOURCE_COMMIT='27a8e54e7ac3c57d6cbf8891f0283dfbaee97317'
SDK_WINDOWS_WHEEL_SHA256='295b67b127f67429ebb9d937bd11448f930bee697fca6a8e0ec1de343ad3c68c'
SDK_FILE_SHA256={
    'adapters/binance/execution.py':'642b91645bea94648a674db4c202072a96b7a184a91c5f91d33792587dba0d2b',
    'adapters/binance/config.py':'838366a8660343c3cace4b60a17917949425e665684fef359973524ffefd39f4',
    'adapters/binance/http/client.py':'b7cc9cfc6c05b8168a9c0181c96156a31e05634d874bcb19199c2ff54f6516c6',
    'adapters/binance/http/endpoint.py':'8f86df89ad9085710ef704866c27a602146dd554ab5da1bb5136173d52a85876',
    'adapters/binance/spot/http/account.py':'5dfe5d19898de6dc8b31ae2df8082dd1cafc1713bd7614fc8ebb512701ced07e',
    'adapters/binance/futures/http/account.py':'53ab44531c556adfc772518d668db7f01047becfc4e6007ee5955c0130e36d5e',
    'core/nautilus_pyo3.cp314-win_amd64.pyd':'0b47c2849a11e7d20661ec6f8053ea94fe90118b219b61c4f6ea7c3cd9795111',
}


def validate_sdk_measurements(*,version,python_version,platform_tag,source_hashes):
    if (version!=SDK_VERSION or python_version!='3.14.2' or platform_tag!='win_amd64'
            or type(source_hashes) is not dict or source_hashes!=SDK_FILE_SHA256):
        raise ValueError('PINNED_SDK_SOURCE_ABI_OR_PLATFORM_CHANGED')


def inspect_installed_sdk():
    import nautilus_trader
    if sys.implementation.name!='cpython':raise ValueError('PINNED_SDK_CPYTHON_REQUIRED')
    root=Path(nautilus_trader.__file__).parent
    platform_tag='win_amd64' if sys.platform=='win32' and platform.machine().upper()=='AMD64' else sys.platform
    measured={path:sha256((root/path).read_bytes()).hexdigest() for path in SDK_FILE_SHA256}
    result=dict(version=importlib.metadata.version('nautilus_trader'),python_version=platform.python_version(),
        platform_tag=platform_tag,source_hashes=measured)
    validate_sdk_measurements(**result)
    return dict(**result,source_commit_reference=SDK_SOURCE_COMMIT,published_wheel_sha256=SDK_WINDOWS_WHEEL_SHA256,
        execution_authorized=False,native_order_qualified=False,external_activation_allowed=False,
        post_wait_last_send_qualification='BLOCKED',linux_deployment='BLOCKED')
