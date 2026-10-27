"""A missing database run or a skipped safety test must not become a green gate."""
from pathlib import Path

import pytest

from tools.ci_paper_postgres import GateError, admin_connection, child_environment, result_counts


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
