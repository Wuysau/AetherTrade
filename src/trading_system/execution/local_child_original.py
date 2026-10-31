"""Original-ID recovery for a local child command; never a resend authority."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trading_system.contracts.order_program import identifier
from trading_system.storage.local_child_commitments import read_local_child_commitments

from .creation_requests import digest, require
from .local_child_admission import LocalChildCommand, read_local_child_command
from .local_child_requests import LocalChildRequest, restore_local_child_request
from .local_program_activation import read_local_activation
from trading_system.storage.local_programs import ProgrammeActivation


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalChildOriginal:
    command: LocalChildCommand
    activation: ProgrammeActivation
    request: LocalChildRequest
    compilation: object

    @property
    def current_state(self) -> str:
        return self.command.state

    @property
    def can_resubmit(self) -> Literal[False]:
        return False

    @property
    def execution_authorized(self) -> Literal[False]:
        return False


def read_local_child_original(tx, command_id: str) -> LocalChildOriginal | None:
    identifier(command_id)
    command = read_local_child_command(tx, command_id)
    if command is None:
        return None
    require(command.scope == tx.scope, 'LOCAL_CHILD_FOREIGN_ORIGINAL')
    activation = read_local_activation(tx, command.activation_id)
    require(activation is not None and activation.authority.scope == tx.scope
            and activation.bundle_hash == command.activation_bundle_hash,
            'LOCAL_CHILD_ORIGINAL_ACTIVATION_REQUIRED')
    snapshot = read_local_child_commitments(tx, command.activation_id)
    item = next((value for value in snapshot.commitments
                 if value.request.request.command_id == command_id), None)
    require(item is not None
            and digest(item.request) == command.request_hash
            and digest(item) == command.commitment_hash
            and digest(item.compilation.parent) == command.parent_hash,
            'LOCAL_CHILD_ORIGINAL_COMMITMENT_CHANGED')
    restore_local_child_request(item.request, item.compilation,
                                activation_id=command.activation_id,
                                command_id=command_id)
    return LocalChildOriginal(command=command, activation=activation,
                              request=item.request, compilation=item.compilation)
