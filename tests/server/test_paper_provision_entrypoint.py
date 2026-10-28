"""The installed CLI must not depend on pytest adding repository scripts."""
import os
import subprocess
import sys


def test_provision_imports_its_helper_without_repository_pythonpath(tmp_path):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    code = '''
import psycopg
from trading_system.server.paper_provision import provision
try:
    provision('invalid_demo_libpq_option=yes', initial_cash='1', journal_directory='unused', isolated_paper=True)
except psycopg.ProgrammingError:
    pass  # Reached DSN validation after importing the packaged provisioner.
else:
    raise AssertionError('invalid DSN was accepted')
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=tmp_path,
        env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
