#!/bin/sh
set -eu
root=/mnt/c/Users/Wuysa/AppData/Local/Temp/aethertrade-sdk-final-send-bfe2fa1/runtime-cp312
parent=/mnt/c/Users/Wuysa/AppData/Local/Temp/aethertrade-sdk-final-send-bfe2fa1
tooling=/mnt/d/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/binance-ope-sdk-final-send/qualification/nautilus/guarded_target
export TMPDIR="$root/tmp" PIP_CACHE_DIR="$root/cache" PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CONSTRAINT="$tooling/build-constraints.txt"
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR"
python="$root/venv/bin/python"
"$python" --version
"$python" -m pip --version
"$python" -m pip install --no-cache-dir --only-binary=:all: "$parent/nautilus_trader-1.231.0-cp312-cp312-manylinux_2_41_x86_64.whl"
"$python" -m pip check
"$python" "$tooling/inspect_wsl_runtime.py" > "$root/runtime.json"
"$python" "$tooling/probe_transport.py" > "$root/transport.json"
set +e
"$python" "$tooling/probe_budget.py" > "$root/budget-red.txt" 2>&1
red=$?
set -e
printf 'candidate1_budget_red_exit=%s\n' "$red"
if [ "$red" -eq 0 ]; then exit 4; fi
cat "$root/runtime.json" "$root/transport.json" "$root/budget-red.txt"
