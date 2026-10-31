from hashlib import sha256
from pathlib import Path
import importlib.metadata,json,platform,sys,sysconfig
import nautilus_trader.core.nautilus_pyo3 as native
h=sha256()
with Path(native.__file__).open('rb') as stream:
    for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
assert h.hexdigest()=='a0792c8eca88012bd9c866810d236e557eb07369b0d915bea206cbddc7e9fa6f'
assert platform.python_version()=='3.12.16' and hasattr(native.HttpClient,'request_guarded')
print(json.dumps(dict(python=platform.python_version(),python_build=platform.python_build(),
    platform=sys.platform,machine=platform.machine(),libc=platform.libc_ver(),abi=sysconfig.get_config_var('SOABI'),
    sdk=importlib.metadata.version('nautilus_trader'),module_sha256=h.hexdigest(),
    guard_signature=native.HttpClient.request_guarded.__text_signature__,
    installed_distributions=sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions()),
    runtime_environment='WSL Ubuntu26.04/glibc2.43; compiled wheel manylinux2.41',
    external_activation_allowed=False),indent=2))
