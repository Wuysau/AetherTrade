"""Bounded natural-language rule candidates, with no compile/enable/publish API.

The explicit English/Chinese sentence form avoids guessing missing semantics:
when close > 100; instrument btc; target 0.01 BASE_QUANTITY; trigger EDGE;
window 60s; bars CLOSED; cooldown 300s; exits STOP,REVIEW; missing HOLD

Conditions accept a single comparison or homogeneous AND/OR lists. Unsupported
or ambiguous language returns graph=None and clarification codes, never code.
After confirmation the separate authorized release flow must compile/publish.
"""
from collections.abc import Callable, Mapping
from decimal import Decimal
import re
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from .providers import DecisionAudit, ModelBoundaryError, bounded_utf8, public_token


_NUMBER = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?'
_CONDITION = re.compile(r'([A-Za-z0-9_:.\-]{1,100})\s*(>=|<=|>|<|==)\s*(' + _NUMBER + r')')
_OPERATORS = {'>': 'GT', '>=': 'GE', '<': 'LT', '<=': 'LE', '==': 'EQ'}
_PREFIXES = {
    'when': 'condition', '当': 'condition', 'instrument': 'instrument', '标的': 'instrument',
    'target': 'target', '目标': 'target', 'trigger': 'trigger', '触发': 'trigger',
    'window': 'window', '窗口': 'window', 'bars': 'bars', 'K线': 'bars',
    'cooldown': 'cooldown', '冷却': 'cooldown', 'exits': 'exits', '退出': 'exits',
    'missing': 'missing', '缺失': 'missing',
}


class RuleDraftGenerator:
    def __init__(self, *, instruments: Mapping[str, t.ScopeKey], audit: DecisionAudit,
                 clock: Callable[[], t.ClockReading]):
        self._instruments = dict(instruments)
        for handle in self._instruments:
            public_token(handle)
        self._audit, self._clock = audit, clock

    @staticmethod
    def _condition(text: str, allowed: tuple[str, ...]) -> t.RuleCondition:
        text = text.replace(' 且 ', ' AND ').replace(' 或 ', ' OR ')
        if ' AND ' in text and ' OR ' in text:
            raise ModelBoundaryError('AMBIGUOUS_BOOLEAN_GROUPING')
        separator = ' AND ' if ' AND ' in text else ' OR ' if ' OR ' in text else None
        parts = text.split(separator) if separator else [text]
        if len(parts) > 16:
            raise ModelBoundaryError('CONDITION_LIMIT')
        children = []
        for part in parts:
            match = _CONDITION.fullmatch(part.strip())
            if match is None or len(match[3]) > 80:
                raise ModelBoundaryError('EXPLICIT_COMPARISON_REQUIRED')
            if match[1] not in allowed:
                raise ModelBoundaryError('INPUT_NOT_ALLOWED')
            children.append(t.RuleCondition(operator=_OPERATORS[match[2]], input_ref=match[1],
                threshold=Decimal(match[3]), children=()))
        if separator is None:
            return children[0]
        return t.RuleCondition(operator='ALL' if separator == ' AND ' else 'ANY',
                               input_ref=None, threshold=None, children=tuple(children))

    @staticmethod
    def _seconds(value: str) -> int:
        match = re.fullmatch(r'(0|[1-9][0-9]{0,7})(?:s|秒)', value)
        if match is None or int(match[1]) > 31_536_000:
            raise ModelBoundaryError('EXPLICIT_SECONDS_REQUIRED')
        return int(match[1])

    def _parse(self, text: str, context: t.RuleDraftContext) -> t.RuleGraph:
        values = {}
        for part in text.replace('；', ';').split(';'):
            pair = part.strip().split(maxsplit=1)
            if len(pair) != 2 or pair[0] not in _PREFIXES:
                raise ModelBoundaryError('EXPLICIT_RULE_SENTENCE_REQUIRED')
            key = _PREFIXES[pair[0]]
            if key in values:
                raise ModelBoundaryError('DUPLICATE_RULE_FIELD')
            values[key] = pair[1]
        if set(values) != set(_PREFIXES.values()):
            raise ModelBoundaryError('MISSING_RULE_FIELDS:' + ','.join(sorted(set(_PREFIXES.values()) - set(values))))
        scope = self._instruments.get(values['instrument'])
        if scope is None or scope not in context.context.scopes:
            raise ModelBoundaryError('INSTRUMENT_NOT_ALLOWED')
        target = re.fullmatch('(' + _NUMBER + r') (BASE_QUANTITY|QUOTE_NOTIONAL)', values['target'])
        if target is None or len(target[1]) > 80 or Decimal(target[1]) < 0:
            raise ModelBoundaryError('EXPLICIT_TARGET_REQUIRED')
        if (values['trigger'] not in ('EDGE', 'CONTINUOUS')
                or values['bars'] not in ('CLOSED', 'INTRABAR')
                or values['missing'] not in ('HOLD', 'REJECT')):
            raise ModelBoundaryError('EXPLICIT_TRIGGER_BAR_MISSING_REQUIRED')
        exits = tuple(item.strip() for item in values['exits'].split(','))
        if (not exits or len(set(exits)) != len(exits)
                or any(item not in ('STOP', 'REVIEW', 'MAX_HOLD', 'THESIS') for item in exits)):
            raise ModelBoundaryError('EXPLICIT_EXIT_PRIORITY_REQUIRED')
        return t.RuleGraph(rule_id=uuid4().hex, revision=1, instrument_ref=scope.instrument_uid,
            condition=self._condition(values['condition'], context.allowed_inputs),
            trigger=values['trigger'], window_seconds=self._seconds(values['window']),
            closed_bar_only=values['bars'] == 'CLOSED', cooldown_seconds=self._seconds(values['cooldown']),
            sizing=target[2], target_value=Decimal(target[1]), exit_priority=exits, missing_data=values['missing'])

    def generate(self, text: str, context: t.RuleDraftContext) -> t.RuleDraft:
        if type(text) is not str or not text.strip() or len(text) > 16000:
            raise ModelBoundaryError('INVALID_RULE_TEXT')
        try:
            bounded_utf8(text)
        except ModelBoundaryError:
            raise ModelBoundaryError('INVALID_RULE_TEXT') from None
        if (context.context.environment == t.Environment.LIVE
                or not context.context.scopes
                or any(s.environment != context.context.environment for s in context.context.scopes)):
            raise ModelBoundaryError('LIVE_OR_INVALID_CONTEXT')
        try:
            graph, ambiguities = self._parse(text.strip(), context), ()
        except ModelBoundaryError as exc:
            graph, ambiguities = None, (str(exc),)
        source = self._audit.append(kind='rule-draft', identifier=uuid4().hex,
            payload={'text': text, 'trust': 'UNTRUSTED_USER_TEXT',
                     'graph': None if graph is None else domain_payload(graph),
                     'ambiguities': list(ambiguities), 'state': 'CANDIDATE_NOT_PUBLISHED'},
            environment=context.context.environment, visible_at=self._clock().utc_now)
        return t.RuleDraft(graph=graph, ambiguities=ambiguities, source_ref=source)
