#!/bin/sh
set -eu
export CARGO_BUILD_JOBS=2 MAX_JOBS=2 CARGO_HOME=/work/cargo RUSTUP_HOME=/work/rustup
export PATH="$CARGO_HOME/bin:$PATH"
export BUILD_MODE=debug-pyo3 CARGO_INCREMENTAL=0
python - <<'PY'
import tarfile
from pathlib import Path
root = Path('/work/linux-source')
root.mkdir(exist_ok=True)
with tarfile.open('/work/source.tar.gz', 'r|gz') as source:
    source.extractall(root,filter='data')
print('stream extraction completed',flush=True)
PY
root=/work/linux-source/nautilus_trader-27a8e54e7ac3c57d6cbf8891f0283dfbaee97317
python /tooling/patch_source.py "$root" > /work/applied.patch
cd "$root"
cargo fetch --locked
export CARGO_NET_OFFLINE=true
python -m pip wheel --no-deps --wheel-dir /work/wheels .
python -m pip install /work/wheels/*.whl
python /tooling/measure_build.py > /work/measured-build.json
python /tooling/probe_transport.py > /work/transport-green.json
cat /work/measured-build.json /work/transport-green.json
