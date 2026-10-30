"""Actual full SDK startup hash must not repeat inside every original window."""
from time import perf_counter
import json
from trading_system.engine.nautilus.guarded_sdk_build import measure_loaded_guarded_sdk
start=perf_counter();first=measure_loaded_guarded_sdk();first_seconds=perf_counter()-start
start=perf_counter();second=measure_loaded_guarded_sdk();second_seconds=perf_counter()-start
assert first==second
assert first['module_sha256']=='a0792c8eca88012bd9c866810d236e557eb07369b0d915bea206cbddc7e9fa6f'
assert not first['execution_authorized']
assert second_seconds<.5,'startup module hash repeated inside original permit window'
print(json.dumps(dict(first_seconds=first_seconds,cached_seconds=second_seconds,measurement=first),indent=2))
