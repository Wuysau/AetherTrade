import copy,json
from pathlib import Path
import pytest
from trading_system.contracts.order_codec import VersionedOrderCodec


def native_vp(version='2026-10-29'):
 p=json.loads(Path('tests/fixtures/orders-v2/program.paper.json').read_text())
 p.update(contract_version=version,template='VP',execution_mode='VENUE_NATIVE',algorithm=dict(kind='VP',duration_seconds=None,urgency='MEDIUM'))
 p['nodes'][0]['role']='ALGORITHM_PARENT'
 return p


def test_native_vp_revision_has_only_documented_algorithm_fields():
 codec=VersionedOrderCodec();p=native_vp();r=codec.decode('order-program',p)
 assert codec.encode('order-program',r)==p
 assert r.algorithm.duration_seconds is None and r.algorithm.urgency=='MEDIUM'
 assert 'participation_bps' not in p['algorithm']


def test_v2_vp_cannot_be_silently_reinterpreted_as_native_v3():
 codec=VersionedOrderCodec();old=native_vp('2026-10-28')
 old['algorithm']=dict(kind='VP',duration_seconds=60,participation_bps='100',urgency='MEDIUM')
 record=codec.decode('order-program',old)
 assert codec.encode('order-program',record)==old
 assert record.algorithm.participation_bps=='100'
 bad=native_vp();bad['algorithm']['participation_bps']='100'
 with pytest.raises((ValueError,__import__('jsonschema').ValidationError)):codec.decode('order-program',bad)


def test_native_twap_still_requires_explicit_duration():
 p=native_vp();p['template']='TWAP';p['algorithm']=dict(kind='TWAP',duration_seconds=None,urgency=None)
 with pytest.raises(ValueError):VersionedOrderCodec().decode('order-program',p)
