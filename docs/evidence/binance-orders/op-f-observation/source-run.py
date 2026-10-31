import sys
sys.path.insert(0, '/tmp/committed-source')
from tools import ci_paper_postgres as gate
gate.SUITES = (('order-observation', '03', 'tests/integration/test_order_observation_postgres.py'),)
raise SystemExit(gate.main(['--evidence-dir', '/handoff/evidence/postgres']))

