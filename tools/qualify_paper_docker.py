"""Retired per-run Docker qualification entrypoint.

The 2026-10-31 Harness requires the shared DEV scheduler. This compatibility
entry accepts old arguments but never creates containers, databases, volumes,
source bundles, credentials or runner directories. Historical qualification
scripts remain in Git history; their results retain their original attribution.
No environment flag can restore the old per-task provisioning path.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]

def dependency_setup(image,*,offline=False):
    if offline:
        if type(image) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
            raise ValueError('offline dependencies require an immutable image ID')
        return None,['python','-m','pip','install','--no-deps','--no-build-isolation','-e','.']
    return ['sh','-c','apt-get update && apt-get install -y --no-install-recommends git'],[
        'python','-m','pip','install','-e','.[test]']



def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit',required=True)
    parser.add_argument('--historical-source-commit',action='append',default=[])
    parser.add_argument('--timeout',type=int,default=5400)
    parser.add_argument('--suite')
    parser.add_argument('--python-suite',action='store_true')
    parser.add_argument('--runner-image',default='python:3.12-slim')
    parser.add_argument('--offline-dependencies',action='store_true')
    parser.parse_args(argv)
    print(json.dumps(dict(status='BLOCKED',tests='NOT_RUN',
        reason='SHARED_DEV_SCHEDULER_REQUIRED',required_environment='DEV',
        environment_created=False,execution_authority=False)))
    return 78


if __name__=='__main__':raise SystemExit(main())
