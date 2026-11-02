import pytest
from tools import fixed_dev_runtime_resources as resources


class Admin:
    def __init__(self,oid=42,active=0):
        self.oid=oid;self.active=active;self.dropped=False;self.row=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,query,params=None):
        text=query if isinstance(query,str) else query.as_string(None)
        if text.startswith('SELECT oid'):
            self.row=None if self.dropped else (self.oid,)
        elif text.startswith('SELECT count'):
            self.row=(self.active,)
        elif text=='DROP DATABASE "paper_runtime_fixture"':
            self.dropped=True
        else:
            raise AssertionError('unexpected SQL')
        return self
    def fetchone(self):return self.row


@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setenv('PAPER_RUNTIME_ADMIN_DSN','fixture-not-a-real-dsn')
    monkeypatch.setattr(resources,'_created',{'paper_runtime_fixture':42})
    events=[]
    monkeypatch.setattr(resources,'record',events.append)
    return events


@pytest.mark.parametrize('oid,active',[(43,0),(42,1)])
def test_identity_change_or_connection_prevents_drop(monkeypatch,recorded,oid,active):
    admin=Admin(oid,active)
    monkeypatch.setattr(resources,'_connect',lambda *a,**kw:admin)
    with pytest.raises(RuntimeError):resources.cleanup()
    assert not admin.dropped
    assert resources._created=={'paper_runtime_fixture':42}
    assert recorded==[]


def test_exact_receipt_removed_only_after_physical_verification(monkeypatch,recorded):
    admin=Admin()
    monkeypatch.setattr(resources,'_connect',lambda *a,**kw:admin)
    resources.cleanup()
    assert admin.dropped and resources._created=={}
    assert recorded==[{'action':'REMOVED','name':'paper_runtime_fixture','oid':42}]


def test_every_registered_console_harness_is_closed_once(monkeypatch):
    calls=[]
    class Harness:
        def close(self):calls.append(self)
    first,second=Harness(),Harness()
    monkeypatch.setattr(resources,'_harnesses',[first,second])
    resources.close_harnesses()
    resources.close_harnesses()
    assert calls==[second,first]
