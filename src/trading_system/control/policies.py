"""Human-reviewed policy/deployment requests; activation belongs to the owner.

A queued policy never changes the effective revision. Owner integration must
atomically fence unsent risk-increasing permits and recheck in-flight work
before making a tighter revision effective. Relaxation needs a fresh exact-body
human review. This stage deliberately exposes no apply/activate function.
"""
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.contracts.wire import ContractCodec
from .auth import ControlError


def single_scope(actor):
    if len(actor.scopes) != 1:
        raise ControlError(503, 'MULTISCOPE_APPROVAL_BINDING_UNAVAILABLE')
    return actor.scopes[0]


class PolicyService:
    def __init__(self, inbox):
        self.inbox = inbox

    def revise(self, policy_id: str, draft: t.RiskPolicyDraft,
               actor: t.ActorContext, key: str) -> t.ControlCommandReceipt:
        policy = ContractCodec().encode(draft.policy)
        # Decode too: encode alone is serialization, not schema validation.
        ContractCodec().decode('risk-policy', policy)
        if (policy['environment'] == 'LIVE' or policy['live_enabled']
                or policy['native_risk_bypass']):
            raise ControlError(403, 'LIVE_NOT_QUALIFIED')
        if draft.author_id != actor.identity_id:
            raise ControlError(403, 'AUTHOR_MISMATCH')
        if (policy['policy_id'] != policy_id
                or policy['environment'] != actor.environment
                or policy['revision'] != draft.expected_revision + 1):
            raise ControlError(409, 'POLICY_CHANGED')

        def validate(tx, grant):
            if policy['collateral_group_id'] != tx.group_id:
                raise ControlError(403, 'COLLATERAL_SCOPE_MISMATCH')

        return self.inbox.enqueue(actor=actor, scope=single_scope(actor),
            operation='reviseRiskPolicy', body={'policy_id': policy_id, 'draft': domain_payload(draft)},
            key=key, expected_revision=draft.expected_revision,
            ownership_epoch=actor.ownership_epoch, review=True, validate=validate)


class DeploymentService:
    def __init__(self, inbox):
        self.inbox = inbox

    def create(self, strategy_id, body, actor, key):
        if body['environment'] == 'LIVE':
            raise ControlError(403, 'LIVE_NOT_QUALIFIED')
        if (body['environment'] != actor.environment
                or body['authorization_id'] != actor.authorization_id):
            raise ControlError(403, 'AUTHORIZATION_SCOPE_MISMATCH')
        # Exact review digest binds strategy ID and immutable revision. This
        # queues approval work; it does not construct a deployment or grant.
        return self.inbox.enqueue(actor=actor, scope=single_scope(actor),
            operation='createDeployment', body={'strategy_id': strategy_id, 'request': body},
            key=key, expected_revision=body['expected_revision'],
            ownership_epoch=actor.ownership_epoch, review=True)
