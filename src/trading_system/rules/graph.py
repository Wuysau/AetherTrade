"""Deterministic compilation of an already-typed rule graph.

A `RuleGraph` is an approved application object (IP-01/`rule-graph.schema.json`).
Nothing here reads natural language, calls `eval`/`exec`, imports a provider
SDK or touches an account. Compilation only proves that the restricted
evaluator can execute the tree and freezes its content hash, so a later
evaluation can refuse a graph that differs from the compiled rule.

Field ownership follows the approved schema exactly. This module is an
application contract validator, not a qualification or authorization gate.
"""
from __future__ import annotations

import re
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload

COMPILER_REVISION = 'ip-20.1'

# Bounds keep evaluation cost finite and replayable; a deeper/larger graph is
# rejected instead of being interpreted with an implicit recursion limit.
MAX_CONDITION_DEPTH = 16
MAX_CONDITION_NODES = 128

COMPARISON_OPERATORS = ('GT', 'GE', 'LT', 'LE', 'EQ')
BOOLEAN_OPERATORS = ('ALL', 'ANY', 'NOT')

# The only feature names this offline engine resolves from an IP-05
# `SnapshotPacket`. The approved snapshot carries quote prices and evidence
# references, so bars, position sizes or sentiment are deliberately absent:
# referencing them is a compile error rather than an assumed default.
SUPPORTED_INPUT_REFS = (
    'quote.ask',
    'quote.bid',
    'quote.last',
    'quote.mid',
    'quote.spread',
)

# DecisionProposal constrains identity strings to this alphabet; the rule graph
# reuses them verbatim, so an uncompilable identifier is rejected up front.
_IDENTIFIER = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)


class RuleCompileError(ValueError):
    """The graph is schema-shaped but not executable by this evaluator."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = tuple(dict.fromkeys(sorted(reasons)))
        super().__init__('; '.join(self.reasons) or 'rule graph rejected')


def graph_hash(graph: t.RuleGraph) -> str:
    """Content hash of the typed graph; identical content gives one digest."""
    return canonical_hash(domain_payload(graph))


def condition_hash(condition: t.RuleCondition) -> str:
    return canonical_hash(domain_payload(condition))


def condition_input_refs(condition: t.RuleCondition) -> tuple[str, ...]:
    """Referenced feature names in deterministic left-to-right order."""
    refs: list[str] = []
    stack = [condition]
    while stack:
        node = stack.pop()
        if type(node) is not t.RuleCondition:
            continue
        if node.operator in COMPARISON_OPERATORS:
            ref = node.input_ref
            if isinstance(ref, str) and ref and ref not in refs:
                refs.append(ref)
        stack.extend(reversed(node.children))
    return tuple(refs)


def _is_int(value: object) -> bool:
    # bool is an int subclass; it is not an acceptable seconds/revision value.
    return type(value) is int


def _condition_reasons(node: object, path: str, depth: int,
                       counter: list[int]) -> list[str]:
    if type(node) is not t.RuleCondition:
        return [f'{path}:CONDITION_NOT_TYPED']
    counter[0] += 1
    if counter[0] > MAX_CONDITION_NODES:
        return [f'{path}:CONDITION_TOO_LARGE']
    if depth > MAX_CONDITION_DEPTH:
        return [f'{path}:CONDITION_TOO_DEEP']

    operator = node.operator
    children = node.children
    if type(children) is not tuple:
        return [f'{path}:CHILDREN_NOT_TUPLE']
    if any(type(child) is not t.RuleCondition for child in children):
        return [f'{path}:CHILD_NOT_TYPED']

    reasons: list[str] = []
    if operator in COMPARISON_OPERATORS:
        if type(node.input_ref) is not str or not node.input_ref.strip():
            reasons.append(f'{path}:INPUT_REF_REQUIRED')
        elif node.input_ref not in SUPPORTED_INPUT_REFS:
            reasons.append(f'{path}:UNSUPPORTED_INPUT:{node.input_ref}')
        if type(node.threshold) is not Decimal:
            reasons.append(f'{path}:THRESHOLD_NOT_DECIMAL')
        elif not node.threshold.is_finite():
            reasons.append(f'{path}:THRESHOLD_NOT_FINITE')
        if children:
            reasons.append(f'{path}:COMPARISON_HAS_CHILDREN')
        return reasons

    if operator in BOOLEAN_OPERATORS:
        if node.input_ref is not None:
            reasons.append(f'{path}:COMBINATOR_INPUT_REF_FORBIDDEN')
        if node.threshold is not None:
            reasons.append(f'{path}:COMBINATOR_THRESHOLD_FORBIDDEN')
        if not children:
            reasons.append(f'{path}:COMBINATOR_REQUIRES_CHILDREN')
        if operator == 'NOT' and len(children) != 1:
            reasons.append(f'{path}:NOT_REQUIRES_ONE_CHILD')
    else:
        return [f'{path}:UNKNOWN_OPERATOR:{operator}']

    for index, child in enumerate(children):
        reasons.extend(_condition_reasons(
            child, f'{path}.{operator}[{index}]', depth + 1, counter))
    return reasons


def _graph_reasons(graph: t.RuleGraph) -> list[str]:
    reasons: list[str] = []

    if type(graph.rule_id) is not str or not graph.rule_id.strip():
        reasons.append('RULE_ID_REQUIRED')
    if not _is_int(graph.revision) or graph.revision < 1:
        reasons.append('REVISION_INVALID')
    if type(graph.instrument_ref) is not str or not _IDENTIFIER.fullmatch(graph.instrument_ref):
        reasons.append('INSTRUMENT_REF_INVALID')
    if graph.trigger not in ('EDGE', 'CONTINUOUS'):
        reasons.append('TRIGGER_INVALID')
    if not _is_int(graph.window_seconds) or graph.window_seconds < 0:
        reasons.append('WINDOW_SECONDS_INVALID')
    if not _is_int(graph.cooldown_seconds) or graph.cooldown_seconds < 0:
        reasons.append('COOLDOWN_SECONDS_INVALID')
    if type(graph.closed_bar_only) is not bool:
        reasons.append('CLOSED_BAR_FLAG_NOT_BOOLEAN')
    if type(graph.closed_bar_only) is bool and graph.closed_bar_only:
        if not _is_int(graph.window_seconds) or graph.window_seconds <= 0:
            # "Closed bar" without a window is not an explicit semantic.
            reasons.append('CLOSED_BAR_REQUIRES_WINDOW')
    if graph.sizing not in ('QUOTE_NOTIONAL', 'BASE_QUANTITY'):
        reasons.append('SIZING_INVALID')
    if type(graph.target_value) is not Decimal or not graph.target_value.is_finite():
        reasons.append('TARGET_VALUE_INVALID')
    if type(graph.missing_data) is not str or graph.missing_data not in ('HOLD', 'REJECT'):
        reasons.append('MISSING_DATA_INVALID')

    priority = graph.exit_priority
    if type(priority) is not tuple or not priority:
        # An empty priority list silently leaves ordinary exit ownership
        # undefined; the evaluator requires the declaration to be explicit.
        reasons.append('EXIT_PRIORITY_REQUIRED')
    elif any(type(entry) is not str or not entry.strip() for entry in priority):
        reasons.append('EXIT_PRIORITY_ENTRY_INVALID')
    elif len(set(priority)) != len(priority):
        reasons.append('EXIT_PRIORITY_DUPLICATED')

    counter = [0]
    reasons.extend(_condition_reasons(graph.condition, 'condition', 1, counter))
    return reasons


class RuleCompiler:
    """Validate a typed graph and freeze its content hash."""

    def compile(self, graph: t.RuleGraph) -> t.CompiledRule:
        if type(graph) is not t.RuleGraph:
            raise RuleCompileError(('GRAPH_NOT_TYPED',))
        reasons = _graph_reasons(graph)
        if reasons:
            raise RuleCompileError(tuple(reasons))
        return t.CompiledRule(graph=graph, hash=graph_hash(graph),
                              compiler_revision=COMPILER_REVISION)
