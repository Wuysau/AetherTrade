"""Generate only additive order schemas; never overwrite the four legacy schemas."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from trading_system.contracts.order_program import schema_for
from trading_system.contracts.order_codec import KINDS
for name,cls in KINDS.items():
    encoded=json.dumps(schema_for(cls),indent=2)+'\n'
    for folder in ('contracts','src/trading_system/schemas'):
        (ROOT/folder/(name+'.v2026-10-28.schema.json')).write_text(encoded,encoding='utf-8')
