"""Offline environment/scheduler status entry.

Reads declared identity, an optional resource inventory and optional scheduler
state. It never contacts Docker, PostgreSQL, TEST or the network, and never
removes resources. Exit 0 means the declarations are internally consistent and
no monitored resource is blocking; it is not OS isolation evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from trading_system.environment.budget import DEFAULT_BUDGET
from trading_system.environment.identity import FIXED_DEV, FIXED_TEST, verify_isolation
from trading_system.environment.resources import ResourceRecord, evaluate_resources
from trading_system.environment.scheduler import RunStatus, TestScheduler


def _records(path):
    if path is None:
        return ()
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError('inventory must be a JSON list')
    return tuple(ResourceRecord(**item) for item in payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', help='JSON list of resource records')
    parser.add_argument('--scheduler', help='scheduler state JSON path')
    parser.add_argument('--disk-used-bytes', type=int, default=0)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        identity = verify_isolation(FIXED_DEV, FIXED_TEST)
        resources = evaluate_resources(_records(args.inventory), DEFAULT_BUDGET,
                                       disk_used_bytes=args.disk_used_bytes)
        runs = {}
        if args.scheduler and Path(args.scheduler).exists():
            scheduler = TestScheduler(args.scheduler, clock=lambda: 0)
            runs = {key: value.status.value for key, value in scheduler.runs.items()
                    if value.status in (RunStatus.QUEUED, RunStatus.RUNNING)}
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'status': 'BLOCKED', 'reason': str(exc)}, sort_keys=True))
        return 2
    status = 'PASS' if identity.status == 'PASS' and resources.status != 'BLOCKED' else 'BLOCKED'
    document = {
        'status': status,
        'isolation': {'status': identity.status, 'findings': list(identity.findings)},
        'resources': {'status': resources.status, 'action': resources.action,
                      'legacy': list(resources.legacy),
                      'reasons': list(resources.reasons)},
        'scheduler_active': runs,
        'trading_mode': 'PAPER',
        'os_permission_isolation': 'NOT_VERIFIED',
        'dev_service': 'NOT_PROBED',
        'note': 'Declarations and offline policy only; no Docker/PostgreSQL/TEST access.',
    }
    if args.json:
        print(json.dumps(document, sort_keys=True))
    else:
        print(f"environment: {status}")
        print(f"isolation: {identity.status} {list(identity.findings)}")
        print(f"resources: {resources.status} {resources.action} {list(resources.reasons)}")
        print(f"active runs: {runs}")
        print('note: no Docker/PostgreSQL/TEST access; OS isolation NOT_VERIFIED')
    return 0 if status == 'PASS' else 78


if __name__ == '__main__':
    raise SystemExit(main())
