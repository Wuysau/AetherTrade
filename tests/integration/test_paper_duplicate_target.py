"""Real PostgreSQL acceptance: in-flight target quantity must not be bought twice."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

from integration.support_ip_07 import ReservationCase, ip07_database, NOW
from trading_system.risk.reservations import ReservationService


def test_two_new_intents_with_same_target_cannot_reserve_twice(ip07_database):
    """Two distinct IDs targeting one owned leg race under the actual group mutex."""
    case = ReservationCase(ip07_database, available='1000')
    first_intent, first_command = case.case.intent, case.command
    suffix = uuid4().hex
    second_intent = replace(first_intent, intent_id='duplicate-' + suffix)
    second_command = replace(first_command, command_id='command-' + suffix,
                             intent_id=second_intent.intent_id, attempt_id='attempt-' + suffix,
                             logical_client_order_id='logical-' + suffix,
                             venue_client_order_id='client-' + suffix)
    commands = {first_intent.intent_id: first_command, second_intent.intent_id: second_command}

    def inputs(tx, intent, book):
        values = case.inputs(tx, intent, book)
        return replace(values, command=commands[intent.intent_id],
                       risk=replace(values.risk, intent_id=intent.intent_id))

    service = ReservationService(case.ledger.uow, case.scope, inputs, clock=lambda: NOW)
    barrier = Barrier(2)

    def run(intent):
        barrier.wait(timeout=10)
        return service.reserve(intent, 1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (first_intent, second_intent)))

    assert sum(result.accepted for result in results) == 1
    assert [case.count(table) for table in ('intents', 'reservations', 'commands', 'outbox')] == [1, 1, 1, 1]
