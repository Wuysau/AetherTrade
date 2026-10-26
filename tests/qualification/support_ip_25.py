"""Synthetic offline inputs driving IP-25/IP-11 code, never qualification."""
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from support_ip_11 import BridgeCase, NOW, compatibility, query
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec, ExecutionCommandCanonicalPayload
from trading_system.engine.nautilus.stocks_adapter import StocksEngineAdapter, StocksEvidenceNormalizer


class StocksCase(BridgeCase):
    def __init__(self, directory):
        super().__init__(directory, product=t.Product.US_EQUITY)
        self.engine = StocksEngineAdapter(compatibility(), offline=self.configuration)
        self.instrument = t.InstrumentSpec(instrument_uid=self.scope.instrument_uid,
            venue='BINANCE', product=t.Product.US_EQUITY, native_symbol='AAPL',
            tick_size=t.Price(value=Decimal('0.01'), currency='USDC'),
            quantity_step=t.Quantity(value=Decimal('0.001'), unit='SHARE'),
            minimum_quantity=t.Quantity(value=Decimal('0.001'), unit='SHARE'),
            minimum_notional=t.Money(amount=Decimal('1'), currency='USDC'), rule_revision=1,
            quote_currency='USDC', settlement_currency='USDC')
        self.set_payload(ExecutionCommandCanonicalPayload(side='BUY', order_type='MARKET',
            quote_notional='100', tokenize=False))
        self.archive = {}
        self.normalizer = StocksEvidenceNormalizer(self.codec, lambda ref: self.archive[ref.id])

    def set_payload(self, payload):
        command = replace(self.command, canonical_payload=payload)
        digest = canonical_hash(ContractCodec().encode(command)['canonical_payload'])
        self.command = replace(command, payload_hash=digest)
        self.permit = replace(self.permit, payload_hash=digest)
        self.trust(self.command, self.permit)

    def raw_ack(self, status='S', *, source='STOCKS_PLACE_ACK', data=None):
        if data is None:
            data = {'status': status, 'orderId': 'observed-order',
                    'clientOrderId': self.command.venue_client_order_id}
        payload = json.dumps(data).encode('utf-8')
        digest = sha256(payload).hexdigest()
        ref = t.EvidenceRef(id='offline:' + digest, hash=digest,
            environment=self.scope.environment, source=source, visible_at=NOW)
        self.archive[ref.id] = payload
        return t.RawVenueMessage(scope=self.scope, source=source, payload_hash=digest,
            raw_payload_ref=ref, received_at=NOW)

    def accept_request_ack_only(self):
        return self.normalizer.observe_ack(self.raw_ack(), actual_shares='0')

    def probe_input(self):
        raw = self.raw_ack()
        evidence = t.VenueCapabilityEvidence(scope=self.scope, build_digest='a' * 64,
            query_scope='balances,shares,order_history,trade_history', source='offline-fixture',
            session_id='synthetic-session', coverage=t.Coverage(scope=self.scope,
                query_succeeded=True, pagination_complete=True, supported=True, missing=()),
            evidence=(raw.raw_payload_ref,))
        return t.StocksProbeInput(evidence=evidence, instrument=self.instrument,
                                  requested_capabilities=('MARKET', 'LIMIT', 'NATIVE_STOP'))


@pytest.fixture
def stocks_case(tmp_path):
    case = StocksCase(tmp_path)
    try:
        yield case
    finally:
        case.codec.close()
