from pathlib import Path
import sys,json,hashlib,tarfile,subprocess,zipfile

artifact=Path('/artifact');work=Path('/work');work.mkdir()
with tarfile.open(artifact/'source.tar') as bundle:bundle.extractall(work,filter='data')
subprocess.run([sys.executable,'-m','pip','wheel','.', '--no-deps','--no-build-isolation','--no-index',
    '--wheel-dir',str(artifact/'wheels')],cwd=work,check=True)
wheel=next((artifact/'wheels').glob('*.whl'))
installed=Path('/wheel-install');installed.mkdir()
with zipfile.ZipFile(wheel) as archive:archive.extractall(installed)
sys.path.insert(0,str(installed))
from trading_system.contracts.resources import contract_path
from trading_system.contracts.management_codec import ManagementCodec
from trading_system.contracts.management_write_v4 import ManagementWriteV4
from trading_system.contracts import types as t
from jsonschema import ValidationError
resource=contract_path('management-write.v2026-10-30-v4.schema.json')
assert resource.is_relative_to(installed),str(resource)
assert resource.read_bytes()==(work/'contracts'/resource.name).read_bytes()
scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='isolated-wheel-account',product=t.Product.SPOT,
    instrument_uid='isolated-wheel-instrument',venue_leg='BOTH')
record=ManagementWriteV4(contract_version='2026-10-30.management-v4',record_kind='PROGRAM_MANAGEMENT_WRITE_V4',
    command_id='isolated-management',original_command_id='isolated-original',scope=scope,program_id='isolated-program',
    service_namespace='BINANCE_SPOT_ORDINARY',original_client_id='original-client',target_venue_id='1',operation='CANCEL',
    owner_strategy_id='isolated-owner',ownership_epoch=1,execution_epoch=2,policy_revision=1,
    plan_hash='a'*64,target_hash='b'*64,request_hash='c'*64,risk_hash='d'*64,funding_hash='e'*64,binding_hash='f'*64,
    state='DURABLE_READY',created_at='2026-10-28T10:00:00Z',valid_until='2026-10-28T10:01:00Z')
codec=ManagementCodec();encoded=codec.encode(record);assert codec.decode(encoded)==record
bad=json.loads(encoded);bad['execution_epoch']=True
try:codec.decode(bad)
except ValidationError:pass
else:raise AssertionError('boolean epoch accepted')
result=dict(source_commit=(artifact/'source-commit.txt').read_text().strip(),wheel=wheel.name,
    wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),resource_sha256=hashlib.sha256(resource.read_bytes()).hexdigest(),
    installed_package=str(installed),roundtrip=True,boolean_epoch_rejected=True,exit_code=0)
(artifact/'proof.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
