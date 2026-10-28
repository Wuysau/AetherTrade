"""Offline installed-candidate probe; never creates clients or a TradingNode."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve().parent


def validate_manifest(manifest, lock_text):
    if manifest['operating_modes'] != {'TESTNET': False, 'DEMO': False, 'LIVE': False}:
        raise ValueError('External execution must stay disabled')
    if manifest['target']['abi'] != 'cp314' or manifest['target']['wheel_platform'] != 'win_amd64':
        raise ValueError('Unexpected candidate platform')
    expected = {f"{a['name']}=={a['version']} --hash=sha256:{a['sha256']}" for a in manifest['artifacts']}
    actual = {line for line in lock_text.splitlines() if line and not line.startswith('#')}
    if actual != expected:
        raise ValueError('Lock differs from artifact manifest')
    if manifest['native_order_qualification'] != 'NOT_RUN':
        raise ValueError('Build inspection cannot grant native qualification')


def run():
    manifest = json.loads((HERE / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest, (HERE / 'requirements-win-cp314.lock').read_text(encoding='utf-8'))
    if sys.version_info[:3] != (3, 14, 2) or sys.platform != 'win32' or platform.machine() != 'AMD64':
        raise RuntimeError('Probe requires exact Windows AMD64 CPython 3.14.2 target')
    for a in manifest['artifacts']:
        if importlib.metadata.version(a['name']) != a['version']:
            raise RuntimeError('Installed version differs: ' + a['name'])
    # No environment credentials are read and no client/node is instantiated.
    import nautilus_trader
    from nautilus_trader.common.component import TestClock
    from nautilus_trader.common.factories import OrderFactory
    from nautilus_trader.model.identifiers import TraderId, StrategyId, InstrumentId
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.enums import OrderSide, TimeInForce
    from nautilus_trader.adapters.binance.config import BinanceExecClientConfig
    from nautilus_trader.adapters.binance.factories import BinanceLiveExecClientFactory
    import nautilus_trader.adapters.binance.execution as execution
    factory = OrderFactory(TraderId('PROBE-001'), StrategyId('PROBE-001'), TestClock())
    order = factory.limit(InstrumentId.from_str('BTCUSDT.BINANCE'), OrderSide.BUY,
                          Quantity.from_str('0.001'), Price.from_str('81490'), time_in_force=TimeInForce.GTC)
    assert str(order.price) == '81490'
    assert order.order_type.name == 'LIMIT'
    source = Path(execution.__file__)
    result = {'status': 'PASS', 'scope': 'installed versions, imports, local LIMIT object only',
              'python': sys.version, 'nautilus_version': nautilus_trader.__version__,
              'installed_dependency_count': len(manifest['artifacts']),
              'execution_module_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'local_order': {'type': order.order_type.name, 'price': str(order.price), 'quantity': str(order.quantity)},
              'binance_config_import': BinanceExecClientConfig.__name__,
              'binance_factory_import': BinanceLiveExecClientFactory.__name__,
              'external_client_constructed': False, 'native_order_qualification': 'NOT_RUN',
              'TESTNET': 'DISABLED', 'DEMO': 'DISABLED', 'LIVE': 'DISABLED'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run()
