#!/bin/sh
set -eu
# NEW container only: existing toolchain-base image, read-only root filesystem.
# /build MUST be the explicitly owned C-drive candidate2 directory.
# Mount /tmp and /run as small tmpfs; bound CPU=2, memory=6GiB, pids=256, logs=10MiB.
export CARGO_HOME=/build/cargo RUSTUP_HOME=/build/rustup CARGO_TARGET_DIR=/build/target
export CARGO_BUILD_JOBS=2 BUILD_MODE=debug-pyo3 CARGO_INCREMENTAL=0
export CARGO_PROFILE_DEBUG_PYO3_DEBUG=0 CFLAGS=-g0 CXXFLAGS=-g0
export TMPDIR=/build/tmp PIP_CACHE_DIR=/build/pip-cache PYTHONPYCACHEPREFIX=/build/pycache
export PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1
export PATH="$CARGO_HOME/bin:$PATH"
if [ -e /build/source/Cargo.toml ]; then
    printf 'Refusing to overwrite an existing candidate build. Inspect its original job first.\n'
    exit 6
fi
mkdir -p /build/cargo /build/rustup/toolchains /build/source /build/wheels "$TMPDIR" "$PIP_CACHE_DIR"
cp -a /inputs/cargo/bin /build/cargo/bin
cp -a /inputs/rustup/toolchains/1.97.1-x86_64-unknown-linux-gnu /build/rustup/toolchains/
rustup default 1.97.1
rustc --version
cargo --version
python --version
python -m venv /build/venv
python=/build/venv/bin/python
cp /inputs/source.tar.gz /build/source.tar.gz
"$python" - <<'PY'
from hashlib import sha256
from pathlib import Path
assert sha256(Path('/build/source.tar.gz').read_bytes()).hexdigest()=='3953eefd58bcfded576d6802d847fa8caf7a4bb7d5def362c8bbb887a162006e'
PY
tar -xzf /build/source.tar.gz -C /build/source --strip-components=1
"$python" /tooling/patch_source.py /build/source > /build/applied.patch
cd /build/source
cargo fetch --locked
export CARGO_NET_OFFLINE=true
"$python" -m pip install --only-binary=:all: -r /tooling/build-constraints.txt
"$python" -m pip freeze > /build/build-environment.txt
"$python" -m pip wheel --no-build-isolation --no-deps --wheel-dir /build/wheels .
"$python" -m pip install --only-binary=:all: /build/wheels/*.whl
"$python" -m pip check > /build/pip-check.txt
"$python" /tooling/measure_build.py --patch /build/applied.patch > /build/measured-build.json
"$python" /tooling/probe_transport.py > /build/transport-green.json
"$python" /tooling/probe_budget.py > /build/budget-green.txt
cat /build/measured-build.json /build/transport-green.json /build/budget-green.txt
