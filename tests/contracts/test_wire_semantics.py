"""Offline contract checks. These do not register or pass T01-T66/Q scenarios."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal, localcontext
import inspect
import json
from typing import get_type_hints

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from contracts.support_ip_01 import KINDS, domain_record, rule_graph, wire_record
from trading_system.contracts import ports, types as domain
from trading_system.contracts.canonical import canonical_hash, canonical_json, decimal_string, domain_payload, utc_timestamp
from trading_system.contracts.wire import ABSENT, ContractCodec, validate_transition


def test_decimal_roundtrip(paper_fixtures):
    exact = '9007199254740993.00000001'
    with localcontext() as context:
        context.prec = 5
        assert str(decimal_string(exact)) == exact
        record = wire_record(paper_fixtures, 'decision-proposal')
        record = replace(record, target=replace(record.target, value=exact))
        assert ContractCodec().encode(record)['target']['value'] == exact
        assert ContractCodec().decode('decision-proposal', ContractCodec().encode(record)) == record


@pytest.mark.parametrize('invalid', [1.1, 1, True, None, Decimal('1'), 'NaN', 'Infinity', '-Infinity', '1e3', '1E-8', '+1', '01', '.1', '1.', '1\n', ' 1', '１'])
def test_decimal_rejects_non_plain_strings(invalid):
    with pytest.raises(ValueError):
        decimal_string(invalid)


@pytest.mark.parametrize('kind', KINDS)
def test_approved_roundtrip_and_immutable_copy(kind, paper_fixtures):
    codec = ContractCodec()
    payload = deepcopy(paper_fixtures[kind])
    record = codec.decode(kind, payload)
    assert codec.encode(record) == payload
    assert codec.hash(record) == canonical_hash(payload)
    payload['contract_version'] = 'tampered'
    assert codec.encode(record)['contract_version'] == '2026-10-20'
    with pytest.raises(FrozenInstanceError):
        record.contract_version = 'tampered'


def test_optional_is_distinct_from_null(paper_fixtures):
    payload = deepcopy(paper_fixtures['decision-proposal'])
    payload.pop('requested_leverage')
    record = ContractCodec().decode('decision-proposal', payload)
    assert record.requested_leverage is ABSENT
    assert 'requested_leverage' not in ContractCodec().encode(record)
    payload['requested_leverage'] = None
    with pytest.raises(ValidationError):
        ContractCodec().decode('decision-proposal', payload)


@pytest.mark.parametrize('kind', ['decision-proposal', 'trade-intent'])
def test_hold_is_semantically_read_only(kind, paper_fixtures):
    before = wire_record(paper_fixtures, kind)
    after = replace(before, action='HOLD')
    validate_transition(before, after)
    assert ContractCodec().encode(before) == paper_fixtures[kind]
    mutations = [replace(after, valid_until='2026-10-20T06:41:00Z'),
                 replace(after, exit_plan=replace(after.exit_plan, maximum_holding_seconds=7200)),
                 replace(after, exit_plan=replace(after.exit_plan, stop_price='48000'))]
    if kind == 'decision-proposal':
        mutations.extend([replace(after, requested_leverage=2),
                          replace(after, target=replace(after.target, value='0.002')),
                          replace(after, entry=replace(after.entry, entry_deadline='2026-10-20T06:42:00Z')),
                          replace(after, entry=replace(after.entry, limit_price='49999'))])
    else:
        mutations.extend([replace(after, target_quantity='0.002'),
                          replace(after, execution=replace(after.execution, entry_deadline='2026-10-20T06:42:00Z')),
                          replace(after, execution=replace(after.execution, limit_price='49999')),
                          replace(after, max_planned_loss=replace(after.max_planned_loss, amount='3'))])
    for mutation in mutations:
        # Every candidate remains structurally valid; rejection is semantic.
        ContractCodec().encode(mutation)
        with pytest.raises(ValueError, match='protected responsibility'):
            validate_transition(before, mutation)


def test_hold_cannot_change_derivative_leverage(paper_fixtures):
    before = wire_record(paper_fixtures, 'trade-intent', product='USD_M_PERP')
    candidate = replace(before, action='HOLD', requested_leverage=2)
    ContractCodec().encode(candidate)
    with pytest.raises(ValueError, match='protected responsibility'):
        validate_transition(before, candidate)


@pytest.mark.parametrize('kind', ['decision-proposal', 'trade-intent'])
def test_update_exit_cannot_change_target(kind, paper_fixtures):
    before = wire_record(paper_fixtures, kind)
    candidate = replace(before, action='UPDATE_EXIT', exit_plan=replace(before.exit_plan, stop_price='49500'))
    validate_transition(before, candidate)
    candidate = (replace(candidate, target=replace(candidate.target, value='0.002'))
                 if kind == 'decision-proposal' else replace(candidate, target_quantity='0.002'))
    with pytest.raises(ValueError):
        validate_transition(before, candidate)


def test_transition_rejects_scope_change(paper_fixtures):
    before = wire_record(paper_fixtures, 'trade-intent')
    for field, value in [('ownership_epoch', 2), ('venue_account_id', 'other'), ('environment', 'TESTNET'), ('authorization_id', 'other')]:
        with pytest.raises(ValueError, match='scope or authority'):
            validate_transition(before, replace(before, **{field: value}))


def test_owner_epoch_wire_name(paper_fixtures):
    codec = ContractCodec()
    payload = codec.encode(wire_record(paper_fixtures, 'execution-command'))
    assert payload['ownership_epoch'] == 1
    assert 'owner_epoch' not in payload
    payload['owner_epoch'] = payload.pop('ownership_epoch')
    with pytest.raises(ValidationError):
        codec.decode('execution-command', payload)


@pytest.mark.parametrize('field', ['account', 'venue_account_id', 'signing_endpoint', 'execution_endpoint', 'headers', 'code', 'authorization_id', 'ownership_epoch', 'policy_revision'])
def test_unknown_fields_rejected(field, paper_fixtures):
    payload = deepcopy(paper_fixtures['decision-proposal'])
    payload[field] = 'untrusted'
    with pytest.raises(ValidationError):
        ContractCodec().decode('decision-proposal', payload)


@pytest.mark.parametrize('kind,child', [('decision-proposal', 'entry'), ('trade-intent', 'execution'), ('execution-command', 'canonical_payload')])
def test_nested_permission_fields_rejected(kind, child, paper_fixtures):
    payload = deepcopy(paper_fixtures[kind])
    payload[child]['headers'] = {'Authorization': 'fictional'}
    with pytest.raises(ValidationError):
        ContractCodec().decode(kind, payload)


@pytest.mark.parametrize('invalid', [1.2, float('nan'), float('inf'), 'NaN', '1e-8', '1\n'])
def test_wire_decimal_rejections(invalid, paper_fixtures):
    payload = deepcopy(paper_fixtures['decision-proposal'])
    payload['target']['value'] = invalid
    with pytest.raises((ValueError, ValidationError)):
        ContractCodec().decode('decision-proposal', payload)


def test_negative_corpus(contract_fixtures):
    cases = json.loads((contract_fixtures / 'negative-cases.json').read_text(encoding='utf-8'))
    for case in cases:
        with pytest.raises((ValueError, ValidationError)):
            ContractCodec().decode(case['schema'].removesuffix('.schema.json'), case['instance'])


def test_canonical_order_and_original_decimal_spelling():
    assert canonical_json({'z': '1.00', 'a': ['中文', True, None]}) == '{"a":["中文",true,null],"z":"1.00"}'
    assert canonical_hash({'b': 2, 'a': 1}) == canonical_hash({'a': 1, 'b': 2})
    assert canonical_hash({'n': '1.0'}) != canonical_hash({'n': '1.00'})


@pytest.mark.parametrize('invalid', [{'nested': [0.1]}, {1: 'value'}, {'n': Decimal('1')}, {'n': (1,)}, []])
def test_canonical_rejects_non_json_or_floating_values(invalid):
    with pytest.raises(ValueError):
        canonical_hash(invalid)


@pytest.mark.parametrize('invalid', ['2026-10-20T06:39:00', '2026-10-20T06:39:00+08:00', '2026-02-30T06:39:00Z', '2026-10-20 06:39:00Z'])
def test_utc_required(invalid):
    with pytest.raises(ValueError):
        utc_timestamp(invalid)


def test_nanosecond_temporal_order(paper_fixtures):
    payload = deepcopy(paper_fixtures['trade-intent'])
    payload.update(as_of='2026-10-20T06:39:00.000000002Z', created_at='2026-10-20T06:39:00.000000001Z')
    with pytest.raises(ValueError, match='ordering'):
        ContractCodec().decode('trade-intent', payload)
    payload['as_of'] = '2026-10-20T06:39:00.000000001+00:00'
    ContractCodec().decode('trade-intent', payload)
    payload['valid_until'] = '2026-10-20T06:39:00.000000000Z'
    with pytest.raises(ValueError, match='ordering'):
        ContractCodec().decode('trade-intent', payload)


def test_money_currency_and_precision():
    with localcontext() as context:
        context.prec = 5
        value = domain.Money(amount=Decimal('9007199254740993.00000001'), currency='USDT')
        result = value + domain.Money(amount=Decimal('0.00000001'), currency='USDT')
        assert str(result.amount) == '9007199254740993.00000002'
    with pytest.raises(ValueError, match='currency'):
        value + domain.Money(amount=Decimal('1'), currency='USD')
    with pytest.raises(ValueError):
        domain.Money(amount=1.0, currency='USD')


def application_schema(repo_root, name, definition=None):
    schema = json.loads((repo_root / 'contracts' / name).read_text(encoding='utf-8'))
    if definition:
        schema.pop('anyOf', None)
        schema['$ref'] = '#/$defs/' + definition
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize('name', sorted({cls.__name__ for cls in vars(domain).values()
                                      if isinstance(cls, type) and is_dataclass(cls)
                                      and cls.__module__ == domain.__name__}))
def test_typed_domain_payloads(name, repo_root, paper_fixtures):
    record = domain_record(getattr(domain, name), paper_fixtures)
    payload = domain_payload(record)
    validator = application_schema(repo_root, 'projection.schema.json', name)
    validator.validate(payload)
    payload['signing_endpoint'] = 'forbidden'
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_nested_wire_omission_in_domain_payload(paper_fixtures):
    command = wire_record(paper_fixtures, 'execution-command')
    event = domain_record(domain.IntentProjection, paper_fixtures)
    payload = domain_payload(replace(event, commands=(command,)))
    assert payload['commands'][0] == ContractCodec().encode(command)


def test_task_sample_domain_accessors(paper_fixtures):
    plan = domain_record(domain.DeltaPlan, paper_fixtures)
    plan = replace(plan, remaining=domain.Quantity(value=Decimal('0.003'), unit='BASE'))
    assert plan.submit_quantity == '0.003'
    report = domain_record(domain.RiskDayResult, paper_fixtures)
    assert report.trading_pnl is None
    report = replace(report, adjusted_pnl=domain.Money(amount=Decimal('10'), currency='USD'))
    assert report.trading_pnl == '10'
    evaluation = domain_record(domain.RuleEvaluation, paper_fixtures)
    proposal = wire_record(paper_fixtures, 'decision-proposal')
    evaluation = replace(evaluation, proposal=proposal, action='PROPOSAL')
    assert evaluation.proposal_hash == ContractCodec().hash(proposal)
    report = domain_record(domain.QualificationReport, paper_fixtures)
    report = replace(report, unsatisfied_gates=('Q10',))
    assert report.live_enabled is False
    assert 'Q10' in report.unsatisfied_gates
    with pytest.raises(ValueError, match='LIVE'):
        replace(report, live_enabled=True)


def test_rule_graph_rejects_code_and_malformed_condition(repo_root):
    validator = application_schema(repo_root, 'rule-graph.schema.json')
    payload = domain_payload(rule_graph())
    validator.validate(payload)
    for key, value in [('code', 'arbitrary()'), ('target_value', 0.1), ('instrument_ref', ''), ('cooldown_seconds', -1)]:
        bad = deepcopy(payload)
        bad[key] = value
        with pytest.raises(ValidationError):
            validator.validate(bad)
    payload['condition']['operator'] = 'ALL'
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_control_transfer_and_gap_contracts(repo_root, paper_fixtures):
    control = domain_payload(domain_record(domain.ControlCommandRequest, paper_fixtures))
    validator = application_schema(repo_root, 'projection.schema.json', 'ControlCommandRequest')
    validator.validate(control)
    control['action'] = 'transfer'
    with pytest.raises(ValidationError):
        validator.validate(control)
    control['transfer'] = domain_payload(domain_record(domain.TransferRequest, paper_fixtures))
    validator.validate(control)
    page = domain_payload(domain_record(domain.EventPage, paper_fixtures))
    page['gap'] = True
    with pytest.raises(ValidationError):
        application_schema(repo_root, 'projection.schema.json', 'EventPage').validate(page)
    page['requires_snapshot'] = True
    application_schema(repo_root, 'projection.schema.json', 'EventPage').validate(page)


def test_ports_are_typed_and_writes_require_permit():
    for name in ('submit', 'cancel', 'amend'):
        hints = get_type_hints(getattr(ports.EnginePort, name))
        assert hints['permit'] is domain.DurablePermit
        assert hints['return'] is domain.CommandReceipt
    assert set(inspect.signature(ports.EnginePort.query).parameters) == {'self', 'query'}
    assert not hasattr(ports.ReadOnlyTools, 'submit')
    for cls in vars(ports).values():
        if isinstance(cls, type) and cls.__module__ == ports.__name__:
            for method in vars(cls).values():
                if inspect.isfunction(method):
                    assert get_type_hints(method)
                    assert not {'api_key', 'secret', 'signature', 'headers'} & set(inspect.signature(method).parameters)


def test_domain_models_have_resolvable_typed_fields():
    for cls in vars(domain).values():
        if isinstance(cls, type) and is_dataclass(cls) and cls.__module__ == domain.__name__:
            hints = get_type_hints(cls)
            assert set(hints) >= {field.name for field in fields(cls)}
            assert all('Any' not in str(hint) and 'dict[' not in str(hint) for hint in hints.values())


def test_openapi_declares_only_domain_resources_and_write_guards(repo_root):
    api = json.loads((repo_root / 'contracts/control-openapi.yaml').read_text(encoding='utf-8'))
    assert api['openapi'] == '3.1.0'
    assert api['servers'] == [{'url': '/api'}]
    assert len(api['paths']) == 11
    for path in api['paths'].values():
        for verb, operation in path.items():
            if verb == 'post':
                headers = {p['name'] for p in operation['parameters'] if p['in'] == 'header' and p['required']}
                assert headers == {'Idempotency-Key', 'If-Match'}
                assert '409' in operation['responses']
                assert '202' in operation['responses']
