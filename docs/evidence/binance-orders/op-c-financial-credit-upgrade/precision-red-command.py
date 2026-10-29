import ast,subprocess,sys
sys.path[:0]=['src','tests']
import pytest
import trading_system.risk.reservation_repository as repository
source=subprocess.check_output(['git','show','e05e165:src/trading_system/risk/reservation_repository.py'],text=True)
function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='increases_program_financial_credit')
namespace={}
exec(compile(ast.Module(body=[function],type_ignores=[]),'<committed-e05e165-helper>','exec'),namespace)
repository.increases_program_financial_credit=namespace[function.name]
sys.exit(pytest.main(['-p','no:cacheprovider','tests/unit/test_program_financial_credit_precision.py','-q','--tb=short','--junitxml=docs/evidence/binance-orders/op-c-financial-credit-upgrade/precision-red.xml']))
