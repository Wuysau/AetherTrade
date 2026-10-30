#!/bin/sh
set -eu
# Fresh disposable Linux container; never execute against the user's host SDK.
# Use the recorded python image digest, --cpus=2 --memory=6g --pids-limit=256.
export CARGO_HOME=/build/cargo RUSTUP_HOME=/build/rustup CARGO_BUILD_JOBS=2
export PATH="$CARGO_HOME/bin:$PATH" BUILD_MODE=debug-pyo3 CARGO_INCREMENTAL=0
export PIP_CONSTRAINT=/tooling/build-constraints.txt
mkdir -p /build/source /build/wheels
apt-get update
apt-get install -y --no-install-recommends curl ca-certificates build-essential pkg-config libssl-dev clang git
curl --fail --location --max-time 120 https://sh.rustup.rs -o /build/rustup-init.sh
sh /build/rustup-init.sh -y --no-modify-path --profile minimal --default-toolchain 1.97.1
cp /inputs/source.tar.gz /build/source.tar.gz
python - <<'PY'
from hashlib import sha256
from pathlib import Path
assert sha256(Path('/build/source.tar.gz').read_bytes()).hexdigest() == '3953eefd58bcfded576d6802d847fa8caf7a4bb7d5def362c8bbb887a162006e'
PY
tar -xzf /build/source.tar.gz -C /build/source --strip-components=1
python /tooling/patch_source.py /build/source > /outputs/native-applied.patch
cd /build/source
cargo fetch --locked
export CARGO_NET_OFFLINE=true
python -m pip wheel --no-deps --wheel-dir /build/wheels .
cp /build/wheels/*.whl /outputs/
python -m pip install /build/wheels/*.whl
python -m pip check > /outputs/pip-check.txt
python /tooling/measure_build.py > /outputs/measured-build.json
python /tooling/probe_transport.py > /outputs/transport-green.json
python /tooling/probe_budget.py > /outputs/budget-green.txt
