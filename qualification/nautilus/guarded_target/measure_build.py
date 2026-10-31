"""Record measured full wheel, never infer compiled artifact from source."""
from pathlib import Path
from hashlib import sha256
import importlib.metadata
import json
import platform
import os
import subprocess
import sys
import sysconfig
import nautilus_trader.core.nautilus_pyo3 as native

def digest(path):
    h=sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--root',default='/build/source')
parser.add_argument('--wheels',default='/build/wheels')
parser.add_argument('--archive',default='/build/source.tar.gz')
parser.add_argument('--patch',default='/outputs/native-applied.patch')
args=parser.parse_args()
root=Path(args.root)
wheels=list(Path(args.wheels).glob('nautilus_trader-1.231.0-*.whl'))
assert len(wheels)==1 and hasattr(native.HttpClient,'request_guarded')
print(json.dumps(dict(schema=1,source_commit='27a8e54e7ac3c57d6cbf8891f0283dfbaee97317',
    version=importlib.metadata.version('nautilus_trader'),python=platform.python_version(),
    platform=sys.platform,machine=platform.machine(),abi=sysconfig.get_config_var('SOABI'),
    libc=platform.libc_ver(),python_build=platform.python_build(),sysconfig_platform=sysconfig.get_platform(),
    rustc=subprocess.check_output(['rustc','--version'],text=True).strip(),
    cargo=subprocess.check_output(['cargo','--version'],text=True).strip(),
    clang=subprocess.check_output(['clang','--version'],text=True).strip(),
    linker=subprocess.check_output(['ld','--version'],text=True).splitlines()[0],
    rustc_binary_sha256=digest(subprocess.check_output(['rustup','which','rustc'],text=True).strip()),
    cargo_binary_sha256=digest(subprocess.check_output(['rustup','which','cargo'],text=True).strip()),
    profile='debug-pyo3',rust_debug_override=os.environ.get('CARGO_PROFILE_DEBUG_PYO3_DEBUG'),
    cflags=os.environ.get('CFLAGS'),cxxflags=os.environ.get('CXXFLAGS'),features=['arrow','cython-compat','extension-module','ffi','postgres','python','tracing-bridge','high-precision'],
    platform_qualification='LOOPBACK_ONLY_NOT_WINDOWS_CP314_OR_VENUE',
    source_archive_sha256=digest(args.archive),patch_sha256=digest(args.patch),
    cargo_lock_sha256=digest(root/'Cargo.lock'),
    original_client_sha256=digest(root/'crates/network/src/http/client.rs'),
    patched_python_http_sha256=digest(root/'crates/network/src/python/http.rs'),
    patched_build_script_sha256=digest(root/'build.py'),
    module_sha256=digest(native.__file__),wheel_sha256=digest(wheels[0]),wheel=wheels[0].name,
    external_activation_allowed=False,qualification='TRANSPORT_AND_PG_PENDING'),indent=2))
