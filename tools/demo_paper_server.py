"""Explicit isolated PAPER demonstration; synthetic quotes and no model calls.

Creates fresh databases through the production provisioner and serves the real
application on a separate loopback port. Never imports an existing account. The
demonstration login is deliberately public and grants only this virtual account.
Requires PAPER_RUNTIME_ADMIN_DSN and PAPER_RUNTIME_ISOLATED_DATABASE=YES.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from trading_system.server.app import create_app
from trading_system.server.config import Settings
from trading_system.server.paper_provision import provision


class ControlledQuotes:
    def snapshot(self):
        at = datetime.now(timezone.utc).isoformat()
        return dict(source='CONTROLLED_PAPER_SCENARIO', status='CONNECTED', last_updated=at,
            error=None, quotes=[dict(symbol=symbol, price=price, change_pct='0', as_of=at,
                source='CONTROLLED_PAPER_SCENARIO', stale=False) for symbol, price in
                [('BTCUSDT', '100'), ('ETHUSDT', '50'), ('SOLUSDT', '10'),
                 ('TSLABUSDT', '20'), ('NVDABUSDT', '20'), ('AAPLBUSDT', '20')]])

    async def refresh(self):
        return self.snapshot()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8797)
    args = parser.parse_args()
    if os.environ.get('PAPER_RUNTIME_ISOLATED_DATABASE') != 'YES':
        raise SystemExit('Explicit isolated PAPER environment required')
    root = Path(__file__).resolve().parents[1]
    directory = root / '.runtime' / ('paper-demo-' + uuid4().hex[:12])
    directory.mkdir(parents=True, exist_ok=False)
    profile = provision(os.environ['PAPER_RUNTIME_ADMIN_DSN'], initial_cash='10000',
        journal_directory=directory / 'journal', output_path=directory / 'profile.json', isolated_paper=True)
    origin = f'http://127.0.0.1:{args.port}'
    config = profile.runtime_kwargs()
    app = create_app(Settings(database_url=profile.console_database_url,
        paper_ledger_dsn=config['ledger_dsn'], paper_venue_dsn=config['venue_dsn'],
        paper_event_dsn=config['event_dsn'], paper_journal_directory=Path(config['journal_directory']),
        model_key_path=directory / 'demo-only-model.key', web_dist=root / 'web/dist',
        testing=True, market_enabled=False, allowed_origins=(origin,), port=args.port), quote_provider=ControlledQuotes())
    store = app.state.store
    store.initialize()
    store.setup('paper-demo', 'paper-demo-only-password', '10000', 12)
    rule = store.create_strategy(dict(name='受控规则风险拒绝示例', symbol='BTCUSDT', mode='RULE',
        budget='10000', target_pct='90', status='ACTIVE', auto_execute=True,
        acknowledge_paper_automation=True, model_profile_ids=[],
        rule=dict(kind='PRICE_THRESHOLD', operator='LTE', price='100', stop_loss_pct='5')))
    asyncio.run(app.state.automation.tick())
    store.update_strategy(rule['id'], dict(status='PAUSED', expected_revision=rule['revision']))
    (root / '.runtime' / 'paper-demo-current.json').write_text(json.dumps(dict(
        origin=origin, profile_path=str(directory / 'profile.json'),
        resolver_profile_path=str(directory / 'profile.resolver.json'),
        source='CONTROLLED_PAPER_SCENARIO', login='paper-demo')) + '\n', encoding='utf-8')
    print('Explicit virtual PAPER demo: ' + origin, flush=True)
    print('Profile directory: ' + str(directory), flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False, log_level='warning')


if __name__ == '__main__':
    main()
