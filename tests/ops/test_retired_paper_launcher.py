"""No Docker or database operation is allowed while testing the retired entry."""
import json
import subprocess
import pytest
from tools import qualify_paper_docker as launcher

@pytest.mark.parametrize('extra',[[],['--python-suite'],['--suite','local-child-storage'],
    ['--offline-dependencies','--runner-image','sha256:'+'a'*64],
    ['--historical-source-commit','b'*40]])
def test_retired_launcher_is_blocked_before_any_process_or_run_directory(monkeypatch,tmp_path,capsys,extra):
    def forbidden(*args,**kwargs):raise AssertionError('retired launcher attempted a process')
    for name in ('run','check_output','Popen'):monkeypatch.setattr(subprocess,name,forbidden)
    monkeypatch.setattr(launcher,'ROOT',tmp_path)
    monkeypatch.setenv('PAPER_CI_ISOLATED','YES')
    monkeypatch.setenv('AETHERTRADE_ALLOW_DOCKER','1')
    assert launcher.main(['--commit','HEAD',*extra])!=0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='BLOCKED' and result['tests']=='NOT_RUN'
    assert result['reason']=='SHARED_DEV_SCHEDULER_REQUIRED'
    assert list(tmp_path.iterdir())==[]
