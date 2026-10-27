"""Explicit real-PostgreSQL console scenarios; never automatic product fills."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest

from trading_system.server.store import Store


def default_market():
    return dict(status='CONNECTED', source='SYNTHETIC_TEST_CONTROLLER', quotes=[
        dict(symbol=symbol, price=price, as_of=datetime.now(timezone.utc).isoformat(), stale=False)
        for symbol, price in [('BTCUSDT', '50000'), ('ETHUSDT', '2000'), ('SOLUSDT', '100')]])


@dataclass(repr=False)
class ConsoleHarness:
    profile: object = field(repr=False)
    store: Store = field(repr=False)
    market_snapshot: object = field(repr=False)
    quotes: dict = field(default_factory=default_market, repr=False)
    _stores: list = field(default_factory=list, repr=False)

    @property
    def database_url(self):
        return self.profile.console_database_url

    @property
    def runtime_config(self):
        return self.profile.runtime_kwargs()

    @property
    def runtime(self):
        return self.store.execution

    @property
    def settings_kwargs(self):
        config = self.runtime_config
        return dict(database_url=self.database_url, paper_ledger_dsn=config['ledger_dsn'],
            paper_venue_dsn=config['venue_dsn'], paper_event_dsn=config['event_dsn'],
            paper_journal_directory=config['journal_directory'])

    def attach(self, store):
        from trading_system.server.paper_runtime import UnifiedPaperRuntime, attach_console_runtime
        runtime = UnifiedPaperRuntime(**self.runtime_config)
        attach_console_runtime(store, runtime, self.market_snapshot or (lambda: self.quotes))
        if store not in self._stores:
            self._stores.append(store)
        return store

    def reopen(self):
        store = self.attach(Store(self.database_url))
        store.initialize()
        return store

    def fill(self, receipt, quotes, *, status='FILLED', cumulative_quantity=None):
        self.quotes = quotes
        order = receipt.get('order', receipt)
        command_id = order['command_id']
        self.runtime.dispatch(command_id)
        quantity = order['quantity'] if cumulative_quantity is None else cumulative_quantity
        price = next(row['price'] for row in quotes['quotes'] if row['symbol'] == order['symbol'])
        self.runtime.inject(command_id, dict(event_id='explicit-controller:' + uuid4().hex,
            status=status, cumulative_quantity=quantity, price=price))
        projected = next(row for row in self.runtime.projection()['orders'] if row['command_id'] == command_id)
        return dict(receipt=receipt, order=projected)

    def execute(self, payload, key, quotes, maximum_age=90):
        self.quotes = quotes
        receipt = self.store.order(payload, key, quotes, maximum_age)
        assert receipt['order']['status'] == 'PENDING'
        return self.fill(receipt, quotes)

    def fill_pending(self, quotes):
        self.quotes = quotes
        results = []
        for order in self.runtime.projection()['orders']:
            if order['status'] == 'PENDING':
                results.append(self.fill(order, quotes))
        return results

    def close(self):
        for store in self._stores:
            store.close()


def provision_console(tmp_path, *, initial_cash='100000', setup=True, market_snapshot=None):
    """Provision three new DBs through the real provisioner, never reuse/drop DBs."""
    from trading_system.server.paper_provision import provision
    admin = os.environ.get('PAPER_RUNTIME_ADMIN_DSN')
    if not admin or os.environ.get('PAPER_RUNTIME_ISOLATED_DATABASE') != 'YES':
        pytest.fail('NOT_RUN: real isolated PostgreSQL console scenario is mandatory')
    directory = Path(tmp_path) / ('console-scenario-' + uuid4().hex)
    directory.mkdir(parents=True)
    profile = provision(admin, initial_cash=initial_cash, journal_directory=directory,
        isolated_paper=True, output_path=None)
    store = Store(profile.console_database_url)
    harness = ConsoleHarness(profile, store, market_snapshot)
    harness.attach(store)
    store.initialize()
    if setup:
        store.setup('owner', 'a-long-password', initial_cash, 24)
    return harness
