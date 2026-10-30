#!/bin/sh
set -eu
# Run ONLY in a new image snapshot after candidate1 reaches a terminal state.
export CARGO_BUILD_JOBS=2 MAX_JOBS=2 CARGO_HOME=/build/cargo RUSTUP_HOME=/build/rustup
export PATH="$CARGO_HOME/bin:$PATH" BUILD_MODE=debug-pyo3 CARGO_INCREMENTAL=0 CARGO_NET_OFFLINE=true
export PIP_CONSTRAINT=/tooling/build-constraints.txt
mkdir -p /outputs/candidate2 /build/wheels-v2
set +e
python /tooling/probe_budget.py > /outputs/budget-red.txt 2>&1
red=$?
set -e
printf 'candidate1_budget_red_exit=%s\n' "$red"
if [ "$red" -eq 0 ]; then exit 4; fi
cp /inputs/python-http.rs /build/source/crates/network/src/python/http.rs
python /tooling/patch_source.py /build/source > /outputs/candidate2/applied.patch
cd /build/source
python -m pip wheel --no-deps --wheel-dir /build/wheels-v2 .
cp /build/wheels-v2/*.whl /outputs/candidate2/
python -m pip install --force-reinstall --no-deps /build/wheels-v2/*.whl
python -m pip check > /outputs/candidate2/pip-check.txt
python /tooling/measure_build.py --wheels /build/wheels-v2 --patch /outputs/candidate2/applied.patch > /outputs/candidate2/measured-build.json
python /tooling/probe_transport.py > /outputs/candidate2/transport-green.json
python /tooling/probe_budget.py > /outputs/candidate2/budget-green.txt
cat /outputs/candidate2/measured-build.json /outputs/candidate2/transport-green.json /outputs/candidate2/budget-green.txt
