"""Offline CLI status regressions; no Docker/PG/TEST access."""
import json

from tools.environment_status import main


def test_status_reports_consistent_identity_and_no_probe(tmp_path, capsys):
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps([]), encoding='utf-8')
    assert main(['--inventory', str(path), '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'PASS' and payload['isolation']['status'] == 'PASS'
    assert payload['resources']['status'] == 'OK'
    assert payload['os_permission_isolation'] == 'NOT_VERIFIED'
    assert payload['dev_service'] == 'NOT_PROBED'
    assert payload['trading_mode'] == 'PAPER'


def test_status_blocks_on_unregistered_resources_without_cleanup(tmp_path, capsys):
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps([{
        'kind': 'CONTAINER', 'resource_id': 'runner-old', 'instance_id': 'dev-cluster-1',
        'owner_task': 'old', 'registered': False, 'bytes': 1}]), encoding='utf-8')
    assert main(['--inventory', str(path), '--json']) == 78
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'BLOCKED'
    assert payload['resources']['action'] == 'BLOCK_NEW_ADMISSION_AND_REPORT_OWNER'
    assert payload['resources']['legacy'] == ['runner-old']


def test_status_rejects_malformed_inputs(tmp_path, capsys):
    broken = tmp_path / 'broken.json'
    broken.write_text('{"not": "a list"}', encoding='utf-8')
    assert main(['--inventory', str(broken)]) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED'
    assert main(['--inventory', str(tmp_path / 'missing.json')]) == 2
