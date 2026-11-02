import os
from pathlib import Path
import subprocess
import sys

from integration.portable_owner_lock import exclusive_file_lock


def test_other_process_excluded_and_release_allows_next_owner(tmp_path):
    lock = tmp_path/'owner.lock'
    code = '''from pathlib import Path
from integration.portable_owner_lock import exclusive_file_lock
import sys
try:
    with exclusive_file_lock(Path(sys.argv[1])): pass
except OSError:
    sys.exit(7)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    def probe():
        return subprocess.run([sys.executable,'-c',code,str(lock)],env=env,timeout=10).returncode
    with exclusive_file_lock(lock):
        assert probe() == 7
    assert probe() == 0


def test_exception_releases_lock(tmp_path):
    import pytest
    lock=tmp_path/'owner.lock'
    with pytest.raises(ValueError):
        with exclusive_file_lock(lock):
            raise ValueError('fixture failure')
    with exclusive_file_lock(lock):
        pass
