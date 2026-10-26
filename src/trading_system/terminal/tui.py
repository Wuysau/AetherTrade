"""Text terminal session used by the CLI/TUI.

The session is a thin presentation and confirmation layer over
``TerminalClient``. It does not compute balances, positions, risk or fills and
does not own a thread, scheduler or execution loop.

Safety rules encoded here
-------------------------
* Closing the terminal sends no control command. It never stops the execution
  owner, protection or an exit workflow. ``owner_running`` is read from an
  injected health projection (the frozen control OpenAPI has no health route)
  and stays ``None`` until that evidence exists.
* ``pause_new``, ``cancel_entries`` and ``exit_owned`` are separate plans.
  None is inferred from another and none is sent by closing the UI.
* An account-level exit that would include manual/EXTERNAL positions needs an
  explicit authorization-scope confirmation. The frozen ``exit_owned`` command
  cannot address EXTERNAL positions, so the result lists them as not covered
  and never claims the account is flat.
* Projection freshness ``age_ms``/``missing``/``stale`` and event gaps are
  displayed as returned by the server. On disconnect the last known age is
  extended by the local monotonic clock and the view says it is disconnected;
  no fresh/complete claim is made.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Callable
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.control.health import EXECUTION_OWNER
from .client import (
    PendingCommand,
    TerminalClient,
    TerminalDenied,
    TerminalError,
    TerminalInvalid,
    TerminalOutcomeUnknown,
)


STOP_ACTIONS = {
    'pause-new': 'pause_new',
    'cancel-entries': 'cancel_entries',
    'exit-owned': 'exit_owned',
}
_UNAVAILABLE_OWNER_STATES = (t.ProcessState.STOPPED,
                            t.ProcessState.SHUTTING_DOWN)


@dataclass(frozen=True, slots=True, kw_only=True)
class ControlBinding:
    """Server-issued CAS binding for a control write.

    The host supplies these from a trusted session/bootstrap read. They are
    never guessed from a local calculation; a stale value is rejected by the
    server with 409.
    """

    expected_revision: int
    ownership_epoch: int

    def __post_init__(self) -> None:
        if (type(self.expected_revision) is not int
                or self.expected_revision < 0
                or type(self.ownership_epoch) is not int
                or self.ownership_epoch < 1):
            raise TerminalInvalid(422, 'INVALID_CONTROL_BINDING')


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountExitConfirmation:
    """Extra confirmation for an account-level exit including EXTERNAL."""

    authorization_scope: tuple[t.ScopeKey, ...]
    include_external: bool

    def __post_init__(self) -> None:
        if (type(self.authorization_scope) is not tuple
                or not self.authorization_scope
                or any(not isinstance(scope, t.ScopeKey)
                       for scope in self.authorization_scope)):
            raise TerminalInvalid(422, 'INVALID_AUTHORIZATION_SCOPE')
        if self.include_external is not True:
            raise TerminalInvalid(422, 'EXTERNAL_SCOPE_NOT_CONFIRMED')
        if any(scope.environment == t.Environment.LIVE
               for scope in self.authorization_scope):
            raise TerminalDenied(403, 'LIVE_NOT_QUALIFIED')


@dataclass(frozen=True, slots=True, kw_only=True)
class StopPlan:
    kind: str
    action: str
    scope: t.ScopeKey
    includes_external: bool
    requires_scope_confirmation: bool
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StopResult:
    plan: StopPlan
    receipts: tuple[t.ControlCommandReceipt, ...]
    external_positions_not_covered: tuple[str, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()
    exit_complete: bool = False
    flat_verified: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseResult:
    """Result of closing the terminal view; never a trading transition."""

    account_commands: list[str] = field(default_factory=list)
    owner_running: bool | None = None
    pending_actions: list[PendingCommand] = field(default_factory=list)
    note: str = ('UI closed. No control command was sent; the execution owner, '
                 'protection and positions are unaffected.')


class TerminalSession:
    """Single-scope terminal view and explicit stop confirmation."""

    def __init__(self, client: TerminalClient, *,
                 scope: t.ScopeKey | None = None,
                 binding: ControlBinding | None = None,
                 health_reader: Callable[[], t.SystemHealth] | None = None,
                 clock: Callable[[], float] = monotonic):
        if not callable(getattr(client, 'query', None)):
            raise TerminalInvalid(400, 'INVALID_TERMINAL_CLIENT')
        self.client = client
        self.scope = scope or client.scope
        if self.scope != client.scope:
            raise TerminalDenied(403, 'AUTHORIZATION_SCOPE_MISMATCH')
        self.binding = binding
        self.health_reader = health_reader
        self.clock = clock
        self.pages: dict[str, t.ProjectionPage] = {}
        self.connected = False
        self.last_error: str | None = None
        self.last_refresh_at: float | None = None
        self.last_age_ms = 0
        self.events: tuple[t.ProjectionEvent, ...] = ()
        self.gap = False
        self.requires_snapshot = False
        self._last_health: t.SystemHealth | None = None
        self._health_error: str | None = None

    # -- reads --------------------------------------------------------------

    def set_binding(self, binding: ControlBinding) -> None:
        self.binding = binding

    def refresh(self, resource: str = 'positions'
                ) -> t.ProjectionPage | None:
        request = t.ProjectionQueryRequest(
            scope=self.scope, resource=resource, owner_strategy_id=None,
            cursor=None, limit=100, maximum_age_ms=None)
        try:
            page = self.client.query(request)
        except TerminalError as exc:
            self.connected = False
            self.last_error = exc.code
            return None
        self.pages[resource] = page
        self.connected = True
        self.last_error = None
        self.last_refresh_at = self.clock()
        self.last_age_ms = page.freshness.age_ms
        return page

    def refresh_events(self, cursor: t.StreamCursor | None = None) -> tuple[
            t.ProjectionEvent, ...]:
        try:
            events = tuple(self.client.follow(cursor))
        except TerminalError as exc:
            self.connected = False
            self.last_error = exc.code
            return ()
        state = self.client.stream_state
        self.events = events
        self.gap = state.gap
        self.requires_snapshot = state.requires_snapshot
        self.connected = True
        self.last_error = None
        self.last_refresh_at = self.clock()
        return events

    # -- explicit control ---------------------------------------------------

    def plan_stop(self, kind: str, *, include_external: bool = False) -> StopPlan:
        action = STOP_ACTIONS.get(kind)
        if action is None:
            raise TerminalInvalid(422, 'INVALID_STOP_ACTION')
        if include_external and action != 'exit_owned':
            raise TerminalInvalid(422, 'EXTERNAL_CONFIRMATION_ONLY_FOR_EXIT')
        summaries = {
            'pause_new': 'pause new entries only',
            'cancel_entries': 'cancel owned entry orders only',
            'exit_owned': 'exit owned positions only',
        }
        return StopPlan(kind=kind, action=action, scope=self.scope,
                        includes_external=bool(include_external),
                        requires_scope_confirmation=bool(include_external),
                        summary=summaries[action])

    def request_stop(self, kind: str, *, key: str | None = None,
                     include_external: bool = False,
                     confirmation: AccountExitConfirmation | None = None
                     ) -> StopResult:
        plan = self.plan_stop(kind, include_external=include_external)
        if include_external:
            if confirmation is None or confirmation.include_external is not True:
                # No request is prepared or sent on this branch.
                raise TerminalDenied(403, 'EXTERNAL_EXIT_CONFIRMATION_REQUIRED')
            if self.scope not in confirmation.authorization_scope:
                raise TerminalDenied(403, 'AUTHORIZATION_SCOPE_MISMATCH')
        if self.binding is None:
            raise TerminalDenied(403, 'CONTROL_BINDING_UNAVAILABLE')

        command_key = key or ('terminal-' + uuid4().hex)
        request = t.ControlCommandRequest(
            action=plan.action, scope=self.scope,
            expected_revision=self.binding.expected_revision,
            ownership_epoch=self.binding.ownership_epoch, transfer=None)
        try:
            receipt = self.client.command(request, command_key)
        except TerminalOutcomeUnknown:
            # Keep the explicit unknown outcome and the caller-provided key so
            # the UI can query the original command instead of resubmitting.
            raise

        reasons: list[str] = []
        external: tuple[str, ...] = ()
        if include_external:
            page = self.pages.get('positions')
            if page is None:
                page = self.refresh('positions')
            if page is None:
                reasons.append('EXTERNAL_POSITION_STATUS_UNKNOWN')
            else:
                external = tuple(sorted(
                    item.position.position_id for item in page.positions
                    if item.position.owner_strategy_id is None))
            reasons.append('EXTERNAL_POSITIONS_REQUIRE_SEPARATE_AUTHORIZATION')
        if receipt.reason:
            reasons.append(receipt.reason)
        return StopResult(plan=plan, receipts=(receipt,),
                          external_positions_not_covered=external,
                          incomplete_reasons=tuple(dict.fromkeys(reasons)),
                          exit_complete=False, flat_verified=False)

    # -- disconnect/close ---------------------------------------------------

    def owner_running(self) -> bool | None:
        if self.health_reader is None:
            return None
        try:
            health = self.health_reader()
        except Exception as exc:  # UI display must degrade, not guess "running"
            self._health_error = type(exc).__name__
            return None
        if not isinstance(health, t.SystemHealth):
            self._health_error = 'INVALID_HEALTH'
            return None
        self._last_health = health
        for process in health.processes:
            if process.process_id == EXECUTION_OWNER:
                return process.state not in _UNAVAILABLE_OWNER_STATES
        return None

    def close(self) -> CloseResult:
        """Close the view without issuing any trading or protection command."""
        return CloseResult(account_commands=[],
                           owner_running=self.owner_running(),
                           pending_actions=list(self.client.pending))

    # -- rendering ----------------------------------------------------------

    def _display_age_ms(self, page: t.ProjectionPage) -> int:
        age = page.freshness.age_ms
        if not self.connected and self.last_refresh_at is not None:
            age += max(0, int((self.clock() - self.last_refresh_at) * 1000))
        return age

    def display(self) -> str:
        scope = self.scope
        lines = [
            'SCOPE environment={} account={} product={} instrument={} leg={}'.format(
                scope.environment, scope.venue_account_id, scope.product,
                scope.instrument_uid, scope.venue_leg),
        ]
        if self.connected:
            lines.append('CONNECTION connected')
        else:
            reason = self.last_error or 'NOT_REFRESHED'
            lines.append('CONNECTION disconnected reason=' + reason)

        for resource in ('positions', 'orders', 'risk-locks'):
            page = self.pages.get(resource)
            if page is None:
                continue
            freshness = page.freshness
            stale = freshness.stale or not self.connected
            lines.append(
                'DATA resource={} as_of={} age_ms={} stale={} missing={}'.format(
                    resource, freshness.as_of, self._display_age_ms(page),
                    str(stale).lower(),
                    ','.join(freshness.missing) if freshness.missing else '-'))
            if resource == 'positions':
                for item in page.positions:
                    position = item.position
                    owner = position.owner_strategy_id or 'EXTERNAL'
                    protection = (item.protection.state
                                  if item.protection is not None else 'NONE')
                    lines.append(
                        'POSITION id={} qty={} {} owner={} ownership_epoch={} '
                        'protection={}'.format(
                            position.position_id,
                            format(position.actual_quantity.value, 'f'),
                            position.actual_quantity.unit, owner,
                            position.ownership_epoch, protection))
            elif resource == 'orders':
                for item in page.orders:
                    order = item.order
                    lines.append(
                        'ORDER id={} lifecycle={} pending={} certainty={} '
                        'leaves={} {}'.format(
                            order.identity.logical_client_order_id,
                            item.lifecycle_state, item.pending_operation,
                            item.command_certainty,
                            format(item.leaves_quantity.value, 'f'),
                            item.leaves_quantity.unit))
            else:
                for lock in page.risk_locks:
                    lines.append(
                        'RISK_LOCK id={} cause={} state={} permission={}'.format(
                            lock.lock_id, lock.cause, lock.state,
                            lock.permission))

        if self.gap:
            lines.append('EVENT_STREAM gap=true requires_snapshot={}'.format(
                str(self.requires_snapshot).lower()))
        elif self.events:
            lines.append('EVENT_STREAM gap=false events={}'.format(
                len(self.events)))

        for pending in self.client.pending:
            lines.append(
                'UNFINISHED action={} key={} outcome=UNKNOWN reason={} '
                '(query the original command before retrying)'.format(
                    pending.action, pending.key, pending.reason))

        owner = self.owner_running()
        lines.append('EXECUTION_OWNER ' + (
            'UNKNOWN' if owner is None else ('RUNNING' if owner else 'STOPPED')))
        return '\n'.join(lines)
