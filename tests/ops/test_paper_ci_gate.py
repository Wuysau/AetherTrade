"""A missing database run or a skipped safety test must not become a green gate."""
from pathlib import Path

import pytest

from tools.ci_paper_postgres import (GateError, admin_connection, child_environment,
                                    clean_libpq_environment, console_database_url, console_scratch,
                                    result_counts, validated_admin_options)


@pytest.mark.parametrize('body,exit_code,accepted', [
    ('<testsuites><testsuite><testcase name="ran"/></testsuite></testsuites>', 0, True),
    ('<testsuites><testsuite><testcase name="not_run"><skipped message="NOT_RUN"/></testcase></testsuite></testsuites>', 0, False),
    ('<testsuites><testsuite><testcase name="failed"><failure/></testcase></testsuite></testsuites>', 0, False),
    ('<testsuites><testsuite><testcase name="error"><error/></testcase></testsuite></testsuites>', 0, False),
    ('<testsuites><testsuite><testcase name="ran"/></testsuite></testsuites>', 1, False),
])
def test_real_suite_result_is_required_and_skips_do_not_pass(tmp_path, body, exit_code, accepted):
    report = tmp_path / 'result.xml'
    report.write_text(body, encoding='utf-8')
    assert result_counts(report, exit_code)['accepted'] is accepted


@pytest.mark.parametrize('body', ['', '<testsuites/>', '<invalid'])
def test_empty_or_malformed_result_is_not_a_pass(tmp_path, body):
    report = tmp_path / 'result.xml'
    report.write_text(body, encoding='utf-8')
    with pytest.raises(GateError):
        result_counts(report, 0)


def test_missing_result_is_not_a_pass(tmp_path):
    with pytest.raises(GateError):
        result_counts(tmp_path / 'absent.xml', 0)


def test_missing_isolated_database_cannot_be_treated_as_green(monkeypatch):
    monkeypatch.delenv('PAPER_CI_ISOLATED', raising=False)
    with pytest.raises(GateError):
        admin_connection()


def test_children_do_not_inherit_real_credentials_or_pytest_selection():
    incoming = {'PATH': '/bin', 'PAPER_ENGINE_ADMIN_DATABASE_DSN': 'do-not-use',
                'IP10_DATABASE_DSN': 'do-not-use', 'OPENAI_API_KEY': 'do-not-use',
                'AETHERTRADE_DATABASE_URL': 'do-not-use', 'PYTEST_ADDOPTS': '-k skip_everything'}
    assert child_environment(incoming) == {'PATH': '/bin'}


@pytest.mark.parametrize('dsn', [
    'postgresql://paper_admin:test@127.0.0.1/paper_ci_admin',
    'postgresql://paper_admin:test@127.0.0.1:5432/paper_ci_admin?hostaddr=10.0.0.1',
    'postgresql://paper_admin:test@127.0.0.1:5432/paper_ci_admin?service=other',
    'postgresql://paper_admin:test@127.0.0.1:5432/paper_ci_admin?options=-csearch_path=other',
    'postgresql://paper_admin:test@127.0.0.1:0/paper_ci_admin',
    'postgresql://other:test@127.0.0.1:5432/paper_ci_admin',
])
def test_admin_rejects_implicit_or_conflicting_connection_routes(dsn):
    with pytest.raises((GateError, ValueError)):
        validated_admin_options(dsn)


def test_console_uses_exact_pinned_admin_address_and_port():
    from sqlalchemy.engine import make_url
    options = validated_admin_options('postgresql://paper_admin:synthetic@localhost:55432/paper_ci_admin')
    result = make_url(console_database_url(options, 'aethertrade_tests'))
    assert result.host == 'localhost' and result.port == 55432
    assert result.database == 'aethertrade_tests' and result.query['hostaddr'] == '127.0.0.1'


def test_libpq_environment_is_cleared_for_connection_and_restored(monkeypatch):
    import os
    monkeypatch.setenv('PGPORT', '55555')
    monkeypatch.setenv('PGHOSTADDR', '192.0.2.1')
    with pytest.raises(RuntimeError):
        with clean_libpq_environment():
            assert 'PGPORT' not in os.environ and 'PGHOSTADDR' not in os.environ
            raise RuntimeError('connection failure')
    assert os.environ['PGPORT'] == '55555' and os.environ['PGHOSTADDR'] == '192.0.2.1'


def test_console_credential_scratch_cannot_live_inside_artifacts(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    options = validated_admin_options('postgresql://paper_admin:synthetic@localhost:55432/paper_ci_admin')
    with pytest.raises(GateError, match='outside uploaded evidence'):
        with console_scratch(options, 'aethertrade_tests', tmp_path):
            pytest.fail('artifact directory must not contain credentials')
    assert list(tmp_path.iterdir()) == []
