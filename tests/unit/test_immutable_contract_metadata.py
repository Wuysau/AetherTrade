"""Cache code metadata only; every changing payload is still decoded/validated."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path

import pytest

from trading_system.contracts import types as t


@dataclass(frozen=True,slots=True,kw_only=True)
class MetadataProbe:
    owner:str
    epoch:int
    debit:t.Money
    credit:t.Money


def test_repeated_decode_reuses_type_metadata_but_never_previous_payload_values(monkeypatch):
    from trading_system.storage import models
    counts=Counter();original=models.get_type_hints
    def count(cls):counts[cls]+=1;return original(cls)
    monkeypatch.setattr(models,'get_type_hints',count)
    payload=dict(owner='original',epoch=1,debit=dict(currency='USD',amount='10'),credit=dict(currency='EUR',amount='20'))
    first=models.decode(MetadataProbe,payload)
    changed={**payload,'owner':'current','epoch':2,'debit':dict(currency='USD',amount='11')}
    second=models.decode(MetadataProbe,changed)
    assert first.owner=='original' and second.owner=='current' and second.epoch==2
    assert first.debit.amount==Decimal(10) and second.debit.amount==Decimal(11)
    with pytest.raises(ValueError):models.decode(MetadataProbe,{**changed,'epoch':False})
    assert counts[MetadataProbe]==1 and max(counts.values())<=1


def test_repeated_versioned_decode_reuses_packaged_validator_without_reusing_validation(monkeypatch):
    from trading_system.contracts import order_codec as m
    builds=[];original=m.Draft202012Validator
    def build(*args,**kwargs):builds.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(m,'Draft202012Validator',build)
    path=Path(__file__).resolve().parents[1]/'fixtures/orders-v2/program.paper.json'
    payload=json.loads(path.read_text(encoding='utf-8'))
    codec=m.VersionedOrderCodec()
    first=codec.decode('order-program',payload)
    second=codec.decode('order-program',{**payload,'program_id':'current-program'})
    assert first.program_id!=second.program_id and second.program_id=='current-program'
    with pytest.raises(Exception):codec.decode('order-program',{**payload,'unexpected':'must-still-deny'})
    assert len(builds)<=1
