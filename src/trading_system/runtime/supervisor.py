"""Takeover eligibility only; never kills hosts, rotates keys or submits orders."""
from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.execution.dispatcher import clock_now
from trading_system.recovery.reconciler import valid_ref
from .execution_owner import shard_key, scope_matches


@dataclass(frozen=True, kw_only=True)
class FenceObservation:
    """Trusted external verifier result, not a claim read from the proof DTO.

    Must bind the old instance, old host/epoch and proposed new host/epoch to
    current isolation evidence. Database lease state is deliberately absent.
    """
    proof: t.ExternalFenceProof
    old_lease: t.OwnerLease
    target: t.ExecutionShard
    external_write_isolated: bool
    expires_at: str
    deadline_monotonic_ns: int


class Supervisor:
    def __init__(self, *, old_lease: t.OwnerLease, scope: t.ScopeKey,
                 read_fence=None, read_recovery=None, clock=clock_now):
        self.old_lease, self.scope = old_lease, scope
        self.read_fence, self.read_recovery, self.clock = read_fence, read_recovery, clock

    def takeover(self, shard: t.ExecutionShard, proof: t.ExternalFenceProof) -> t.TakeoverVerdict:
        missing = []
        verified = None
        try:
            old = self.old_lease
            now = self.clock()
            if (shard.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
                    or not scope_matches(self.scope, shard) or shard_key(shard) != shard_key(old.shard)
                    or type(shard.execution_epoch) is not int or shard.execution_epoch <= old.shard.execution_epoch
                    or not shard.node_id or now.clock_health != 'HEALTHY'
                    or type(now.monotonic_ns) is not int or now.monotonic_ns < 0):
                missing.append('OFFLINE_SCOPE_AND_NEW_EPOCH_REQUIRED')
            if (not isinstance(proof, t.ExternalFenceProof) or proof.shard != old.shard
                    or proof.old_instance_id != old.instance_id or not old.instance_id
                    or proof.method not in {'KEY_REVOKED', 'NETWORK_BLOCKED', 'PROCESS_TERMINATED'}
                    or not valid_ref(proof.evidence, self.scope, now.utc_now, 60)):
                missing.append('EXACT_OLD_HOST_FENCE_PROOF_REQUIRED')
            if not missing:
                observation = self.read_fence(proof, shard) if self.read_fence else None
                if (not isinstance(observation, FenceObservation) or observation.proof != proof
                        or observation.old_lease != old or observation.target != shard
                        or observation.external_write_isolated is not True
                        or type(observation.deadline_monotonic_ns) is not int
                        or now.monotonic_ns >= observation.deadline_monotonic_ns
                        or utc_timestamp(now.utc_now) >= utc_timestamp(observation.expires_at)):
                    missing.append('EXTERNAL_ISOLATION_UNVERIFIED_LEASE_IS_NOT_FENCE')
                else:
                    verified = proof
            if not missing:
                # Trusted reader must merge emergency evidence, use IP-13 gate,
                # and bind all scopes in the shard to this target epoch. This
                # result is eligibility, not a promoted process or a write permit.
                recovery = self.read_recovery(shard) if self.read_recovery else None
                if (not isinstance(recovery, t.RecoveryVerdict) or recovery.ready is not True
                        or recovery.missing or recovery.risk_locks):
                    missing.append('POST_FENCE_SHARD_RECOVERY_REQUIRED')
                # Recheck external isolation after potentially slow recovery.
                current = self.read_fence(proof, shard)
                after = self.clock()
                if (current != observation or after.clock_health != 'HEALTHY'
                        or after.monotonic_ns < now.monotonic_ns
                        or after.monotonic_ns >= observation.deadline_monotonic_ns
                        or utc_timestamp(after.utc_now) < utc_timestamp(now.utc_now)
                        or utc_timestamp(after.utc_now) >= utc_timestamp(observation.expires_at)
                        or not valid_ref(proof.evidence, self.scope, after.utc_now, 60)):
                    missing.append('FENCE_CHANGED_DURING_RECOVERY')
        except Exception:
            missing.append('TAKEOVER_EVIDENCE_UNAVAILABLE')
        return t.TakeoverVerdict(allowed=not missing, missing=tuple(dict.fromkeys(missing)), fence_proof=verified)
