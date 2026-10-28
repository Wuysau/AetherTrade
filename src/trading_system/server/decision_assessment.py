"""Non-authorizing assessments; approved execution wire contracts stay unchanged."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import TypedDict, Literal
from jsonschema import ValidationError
from ..contracts.wire import ContractCodec, validate_transition

class DecisionAssessment(TypedDict):
    schema_revision: Literal['assessment_v1']
    summary: str
    proposal: dict | None  # decoded into the existing immutable DecisionProposal below
    evidence_claims: list[dict]
    watch_conditions: list[dict]
    next_review_at: str | None


SCHEMA = 'assessment_v1'
PROMPT_REVISION = 'decision-evidence-3'


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result


def text(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid text')
    return value


def number(value):
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError('Decimal string required')
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError('Finite decimal required')
    return result


def field(snapshot, path):
    if not isinstance(path, str) or len(path) > 200 or not path.startswith(('market.', 'costs.', 'constraints.', 'portfolio.')):
        raise ValueError('Predicate field requires a full snapshot path, e.g. market.quote.price or market.features.5m.atr; bare field names are not supported')
    value = snapshot
    for part in path.split('.'):
        value = value[part]
    return value


def predicate(snapshot, value):
    if not isinstance(value, dict) or set(value) != {'field', 'operator', 'value'}:
        raise ValueError('Invalid predicate')
    left = field(snapshot, value['field'])
    op = value['operator']
    right = value['value']
    if op == 'EQ':
        if type(right) not in (str, bool, int) or type(left) != type(right):
            raise ValueError('Invalid equality')
        return left == right
    left, right = number(left), number(right)
    if op == 'LT': return left < right
    if op == 'GT': return left > right
    raise ValueError('Unsupported predicate')


def validate_assessment(value: dict, snapshot: dict) -> DecisionAssessment:
    if not isinstance(value, dict) or set(value) != {'schema_revision', 'summary', 'proposal', 'evidence_claims', 'watch_conditions', 'next_review_at'} or value['schema_revision'] != SCHEMA:
        raise ValueError('Invalid assessment schema')
    text(value['summary'])
    refs = snapshot['evidence_refs']
    now = datetime.now(timezone.utc)
    if value['next_review_at'] is not None and instant(value['next_review_at']) <= now:
        raise ValueError('Expired review')
    claims = value['evidence_claims']
    if not isinstance(claims, list) or len(claims) > 20:
        raise ValueError('Invalid evidence claims')
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {'evidence_ref', 'predicate', 'conclusion'} or claim['evidence_ref'] not in refs:
            raise ValueError('Unknown evidence')
        text(claim['conclusion'])
        if claim['predicate'] is not None and not predicate(snapshot, claim['predicate']):
            raise ValueError('False evidence claim')
    conditions = value['watch_conditions']
    if not isinstance(conditions, list) or len(conditions) > 10:
        raise ValueError('Invalid watch conditions')
    for condition in conditions:
        if not isinstance(condition, dict) or set(condition) != {'kind', 'evidence_ref', 'value'} or condition['evidence_ref'] not in refs:
            raise ValueError('Invalid watch reference')
        if condition['kind'] == 'NEW_BAR':
            if condition['value'] is not None or not condition['evidence_ref'].startswith('candles:'):
                raise ValueError('Invalid bar condition')
        elif condition['kind'] in ('PRICE_ABOVE', 'PRICE_BELOW'):
            if condition['evidence_ref'] != 'quote' or number(condition['value']) <= 0:
                raise ValueError('PRICE_ABOVE/PRICE_BELOW requires evidence_ref=quote and a positive decimal-string value; NEW_BAR uses candles:<interval>')
        else:
            raise ValueError('Unsupported watch condition')
    proposal = value['proposal']
    if proposal is None:
        return value
    codec = ContractCodec()
    try:
        typed = codec.decode('decision-proposal', proposal)
    except (ValidationError, TypeError, KeyError) as exc:
        raise ValueError('Invalid DecisionProposal') from exc
    if typed.instrument_ref != snapshot['instrument_ref'] or any(ref not in refs for ref in typed.evidence_refs):
        raise ValueError('Proposal scope/evidence mismatch')
    allowed = snapshot['task_profile'].get('allowed_operations', [])
    if typed.action not in allowed:
        raise ValueError('Operation not allowed')
    if instant(typed.valid_until) <= now or instant(typed.entry.entry_deadline) > instant(typed.valid_until) or instant(typed.entry.entry_deadline) <= now:
        raise ValueError('Expired proposal or invalid deadline')
    horizon = snapshot['task_profile'].get('holding_period_seconds')
    if horizon is not None and (typed.exit_plan.maximum_holding_seconds > horizon or (instant(typed.valid_until)-now).total_seconds() > horizon):
        raise ValueError('Plan exceeds authorized horizon')
    if instant(typed.exit_plan.review_due_at) <= now:
        raise ValueError('Expired exit review')
    if proposal.get('requested_leverage', 1) != 1 or (snapshot['product'] == 'SPOT' and typed.target.side == 'SHORT'):
        raise ValueError('Unsupported leverage/side')
    if typed.action in ('HOLD', 'UPDATE_EXIT'):
        previous = snapshot['portfolio'].get('previous_proposal')
        if previous is None:
            raise ValueError('No existing responsibility to preserve')
        validate_transition(codec.decode('decision-proposal', previous), typed)
    price = number(snapshot['market']['quote']['price'])
    if typed.entry.order_preference == 'LIMIT':
        if typed.entry.limit_price is None: raise ValueError('Missing limit price')
        price = number(typed.entry.limit_price)
    elif typed.entry.limit_price is not None:
        raise ValueError('Market entry has limit price')
    target = number(typed.target.value)
    notional = target * price if typed.target.sizing == 'BASE_QUANTITY' else target
    constraints = snapshot['constraints']
    maximum = constraints.get('maximum_target_notional')
    equity = snapshot['portfolio'].get('account', {}).get('equity', snapshot['portfolio'].get('equity'))
    if maximum is None and equity is not None and constraints.get('max_position_pct') is not None:
        maximum = str(number(equity) * number(str(constraints['max_position_pct'])) / 100)
    if maximum is None or notional > number(maximum):
        raise ValueError('Unknown/exceeded target budget')
    if typed.target.side != 'FLAT':
        stop = typed.exit_plan.stop_price
        if stop is None or (typed.target.side == 'LONG' and number(stop) >= price) or (typed.target.side == 'SHORT' and number(stop) <= price):
            raise ValueError('Invalid protective stop')
        if constraints.get('protection_supported') is not True:
            raise ValueError('Protection capability unavailable')
        if snapshot['costs'].get('complete') is not True:
            raise ValueError('Incomplete costs')
    return value


def validate_review(value, snapshot):
    if not isinstance(value, dict) or set(value) != {'schema_revision', 'issues'} or value['schema_revision'] != 'review_v1' or not isinstance(value['issues'], list) or len(value['issues']) > 20:
        raise ValueError('Invalid review schema')
    statuses = []
    issues = []
    for issue in value['issues']:
        if not isinstance(issue, dict) or set(issue) != {'category', 'evidence_ref', 'predicate', 'severity', 'suggestion'}:
            raise ValueError('Invalid issue')
        if issue['category'] not in ('FACT', 'PREFERENCE') or issue['severity'] not in ('HARD', 'SOFT') or issue['evidence_ref'] not in (snapshot['evidence_refs'] + (snapshot['task_profile'].get('optional', []) if issue['category'] == 'PREFERENCE' and issue['severity'] == 'SOFT' else [])):
            raise ValueError('Invalid issue scope')
        text(issue['suggestion'])
        status = 'SOFT'
        if issue['category'] == 'FACT' or issue['severity'] == 'HARD':
            try:
                status = 'HARD' if predicate(snapshot, issue['predicate']) else 'DISPROVED'
            except (ValueError, TypeError, KeyError):
                status = 'UNVERIFIED'
        statuses.append(status)
        issues.append(dict(issue, verification=status))
    status = next((s for s in ('HARD', 'UNVERIFIED', 'SOFT') if s in statuses), 'VERIFIED')
    return {'status': status, 'issues': issues}
