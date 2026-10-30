"""Record measured full wheel, never infer compiled artifact from source."""
from pathlib import Path
from hashlib import sha256
import importlib.metadata
import json
import platform
import subprocess
import sys
import sysconfig
import nautilus_trader.core.nautilus_pyo3 as native

def digest(path):return sha256(Path(path).read_bytes()).hexdigest()
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
    rustc=subprocess.check_output(['rustc','--version'],text=True).strip(),
    cargo=subprocess.check_output(['cargo','--version'],text=True).strip(),
    source_archive_sha256=digest(args.archive),patch_sha256=digest(args.patch),
    cargo_lock_sha256=digest(root/'Cargo.lock'),
    module_sha256=digest(native.__file__),wheel_sha256=digest(wheels[0]),wheel=wheels[0].name,
    external_activation_allowed=False,qualification='TRANSPORT_AND_PG_PENDING'),indent=2))
