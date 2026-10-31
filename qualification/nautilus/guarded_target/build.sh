#!/bin/sh
set -eu
# Isolated container only; bounded by docker --cpus=2 --memory=6g --pids-limit=256.
export CARGO_BUILD_JOBS=2 MAX_JOBS=2 CARGO_HOME=/work/cargo RUSTUP_HOME=/work/rustup
export PATH="$CARGO_HOME/bin:$PATH"
export BUILD_MODE=debug-pyo3
export CARGO_INCREMENTAL=0
mkdir -p /work/source /work/wheels
apt-get update
apt-get install -y --no-install-recommends curl ca-certificates build-essential pkg-config libssl-dev clang git
curl --fail --location --max-time 120 https://sh.rustup.rs -o /work/rustup-init.sh
sh /work/rustup-init.sh -y --no-modify-path --profile minimal --default-toolchain 1.97.1
rustc --version
cargo --version
python --version
tar -xzf /work/source.tar.gz -C /work/source --strip-components=1
python /tooling/patch_source.py /work/source > /work/applied.patch
cd /work/source
python -m pip wheel --no-deps --wheel-dir /work/wheels .
python -m pip install /work/wheels/*.whl
python /tooling/probe_transport.py
