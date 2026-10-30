#!/bin/sh
set -eu
# Follow-up after terminal extraction failures. All high-I/O build state is
# container-native; host mount is used only for input archive and final artifacts.
export CARGO_BUILD_JOBS=2 MAX_JOBS=2 CARGO_HOME=/build/cargo RUSTUP_HOME=/build/rustup
export PATH="$CARGO_HOME/bin:$PATH" BUILD_MODE=debug-pyo3 CARGO_INCREMENTAL=0
mkdir -p /build/cargo /build/rustup/toolchains /build/source /build/wheels
cp -a /inputs/cargo/bin /build/cargo/bin
cp -a /inputs/rustup/toolchains/1.97.1-x86_64-unknown-linux-gnu /build/rustup/toolchains/
rustup default 1.97.1
rustc --version
cp /inputs/source.tar.gz /build/source.tar.gz
tar -xzf /build/source.tar.gz -C /build/source --strip-components=1
printf 'NATIVE_FILESYSTEM_EXTRACTION_COMPLETE\n'
python /tooling/patch_source.py /build/source > /outputs/native-applied.patch
cd /build/source
cargo fetch --locked
export CARGO_NET_OFFLINE=true
python -m pip wheel --no-deps --wheel-dir /build/wheels .
cp /build/wheels/*.whl /outputs/
python -m pip install /build/wheels/*.whl
python /tooling/probe_transport.py > /outputs/transport-green.json
cat /outputs/transport-green.json
