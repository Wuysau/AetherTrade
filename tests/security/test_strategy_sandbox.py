"""Offline boundary regressions and opt-in native probes, not T/Q certification."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Event, Timer
import time

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload
from trading_system.decision.memory import MemoryStore
from trading_system.rules.releases import OfflineReleaseRepository, ReleaseDenied, ReleaseService
from trading_system.rules.sandbox import LinuxRunner, RuleSandbox, SandboxDenied
from security.support_ip_21 import (
    NOW, LATER, HOLD, bundle, evidence, limits, rule_input, offline_sandbox,
    release_case, sandbox_case,
)


def test_unconfigured_sandbox_fails_closed():
    artifact, content = bundle()
    result = RuleSandbox(load=lambda ref: pytest.fail('must not load')).run(artifact, rule_input(), limits())
    assert result.action == 'REJECT' and result.reasons == ('NATIVE_RUNNER_NOT_CONFIGURED',)
    assert result.proposal is None


@pytest.mark.parametrize('field,value', [('network', 'ALLOW'), ('credentials', 'HOST'),
    ('cpu_ms', 0), ('cpu_ms', True), ('memory_bytes', -1), ('output_bytes', 1048577)])
def test_unsafe_limits_rejected_before_worker(field, value):
    service, runner, artifact = offline_sandbox()
    result = service.run(artifact, rule_input(), limits(**{field: value}))
    assert result.action == 'REJECT' and runner.calls == []


@pytest.mark.parametrize('field,value', [('hash', 'b' * 64),
    ('dependency_lock_hash', 'c' * 64), ('hash', 'not-a-hash')])
def test_artifact_tamper_rejected(field, value):
    service, runner, artifact = offline_sandbox()
    assert service.run(replace(artifact, **{field: value}), rule_input(), limits()).action == 'REJECT'
    assert not runner.calls


@pytest.mark.parametrize('state', [t.RuleState(previous_condition=1, last_triggered_at=None,
    last_bar_id=None, revision=0), t.RuleState(previous_condition=None, last_triggered_at=None,
    last_bar_id='x' * 257, revision=0), t.RuleState(previous_condition=None,
    last_triggered_at=None, last_bar_id=None, revision=True)])
def test_invalid_rule_state_never_reaches_worker(state):
    service, runner, artifact = offline_sandbox()
    assert service.run(artifact, replace(rule_input(), state=state), limits()).reasons == ('INVALID_STATE',)
    assert runner.calls == []


def test_input_size_limit_precedes_worker():
    service, runner, artifact = offline_sandbox()
    data = rule_input()
    data = replace(data, snapshot=replace(data.snapshot, published_at='x' * (1024**2 + 1)))
    assert service.run(artifact, data, limits()).reasons == ('INPUT_LIMIT',)
    assert runner.calls == []


def test_native_runner_has_no_unsupported_platform_fallback(tmp_path, monkeypatch):
    import trading_system.rules.sandbox as module
    monkeypatch.setattr(module.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not spawn'))
    with pytest.raises(SandboxDenied, match='LINUX_X86_64_REQUIRED'):
        LinuxRunner(rootfs=tmp_path).run(bundle()[1], b'{}', limits())


@pytest.mark.parametrize('fault,reason', [('missing-mount', 'MOUNTPOINT'),
    ('nonempty-mount', 'MOUNTPOINT'), ('lock-mismatch', 'DEPENDENCY_MISMATCH'),
    ('pre-cancel', 'CANCELLED')])
def test_native_preflight_fails_before_process_creation(tmp_path, monkeypatch, fault, reason):
    # OS identity is a double only to exercise preflight; no isolation claim.
    import trading_system.rules.sandbox as module
    monkeypatch.setattr(module.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(module.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not spawn'))
    content = bundle()[1]
    for name in ('job', 'tmp', 'proc', 'dev'):
        if fault != 'missing-mount' or name != 'dev':
            (tmp_path / name).mkdir()
    (tmp_path / 'dependency.lock').write_bytes(content.dependency_lock)
    if fault == 'nonempty-mount':
        (tmp_path / 'dev' / 'unexpected').write_bytes(b'')
    if fault == 'lock-mismatch':
        (tmp_path / 'dependency.lock').write_bytes(b'different lock')
    cancel = Event()
    if fault == 'pre-cancel':
        cancel.set()
    with pytest.raises(SandboxDenied, match=reason):
        LinuxRunner(rootfs=tmp_path, cancel=cancel).run(content, b'{}', limits())


def test_only_research_and_minimal_input():
    service, runner, artifact = offline_sandbox()
    data = rule_input()
    result = service.run(artifact, data, limits())
    assert result.action == 'HOLD' and result.state == data.state
    payload = json.loads(runner.calls[0][1])
    assert set(payload) == {'quote', 'published_at', 'state'}
    for environment in (t.Environment.PAPER, t.Environment.TESTNET, t.Environment.LIVE):
        altered = replace(data, snapshot=replace(data.snapshot,
            scope=replace(data.snapshot.scope, environment=environment)))
        assert service.run(artifact, altered, limits()).reasons == ('RESEARCH_ONLY',)
    assert len(runner.calls) == 1


@pytest.mark.parametrize('output', [b'not-json', b'[]', b'{"action":"HOLD","proposal":null,"code":"x"}',
    b'{"action":"HOLD","action":"HOLD","proposal":null}',
    b'{"action":"PROPOSAL","proposal":{"account":"evil"}}',
    b'{"action":"HOLD","proposal":NaN}', pytest.param(b'x' * 65537, id='oversized-65537-bytes'),
    RuntimeError('sensitive-host-message')])
def test_output_is_bounded_untrusted_and_strict(output):
    service, _, artifact = offline_sandbox(output)
    result = service.run(artifact, rule_input(), limits())
    assert result.action == 'REJECT' and result.proposal is None
    assert 'sensitive-host-message' not in str(result)


def test_proposal_still_requires_hard_risk():
    from unit.support_ip_09 import make_case
    from unit.support_ip_06 import make_case as risk_case
    candidate = make_case().proposal
    candidate = replace(candidate, target=replace(candidate.target, value='999999999'))
    output = json.dumps({'action': 'PROPOSAL', 'proposal': domain_payload(candidate)}).encode()
    service, _, artifact = offline_sandbox(output)
    result = service.run(artifact, rule_input(), limits())
    assert result.action == 'PROPOSAL'
    assert result.proposal.target.value == '999999999'  # sandbox is not risk approval
    assert result.reasons == ('UNTRUSTED_REQUIRES_NORMAL_RISK_INGRESS',)
    # Real risk calculator, synthetic market/ledger: not registered T59 evidence.
    risk = risk_case()
    assert risk.evaluate().verdict == 'ALLOW'
    risk.intent = replace(risk.intent, target_quantity=result.proposal.target.value)
    decision = risk.evaluate()
    assert decision.verdict != 'ALLOW'
    assert 'REPLAN:ORDER_NOTIONAL' in decision.rejection_codes


def test_release_requires_permission(release_case):
    c = release_case
    candidate = c.service.propose(c.change())
    with pytest.raises(ReleaseDenied, match='PERMISSION'):
        c.service.activate(candidate, c.approval(candidate))
    assert c.repository.history('strategy') == ()
    c.permitted = True
    first = c.service.activate(candidate, c.approval(candidate))
    second_candidate = c.service.propose(c.change(b'second'))
    second = c.service.activate(second_candidate, c.approval(second_candidate))
    rollback = c.service.propose_rollback('strategy', 1, evidence())
    c.permitted = False
    with pytest.raises(ReleaseDenied):
        c.service.activate(rollback, c.approval(rollback))
    c.permitted = True
    third = c.service.activate(rollback, c.approval(rollback))
    assert [r.revision for r in c.repository.history('strategy')] == [1, 2, 3]
    assert third.rollback_revision == first.revision
    assert third.candidate.change.artifact == first.candidate.change.artifact
    assert second.candidate.change.artifact != first.candidate.change.artifact
    assert third.candidate.change.artifact.dependency_lock_hash
    with pytest.raises(ReleaseDenied, match='STALE_OR_CONSUMED'):
        c.service.activate(rollback, c.approval(rollback))


def test_stale_candidate_cannot_replace_revision(release_case):
    c = release_case
    first, stale = c.service.propose(c.change()), c.service.propose(c.change(b'another'))
    c.permitted = True
    c.service.activate(first, c.approval(first))
    with pytest.raises(ReleaseDenied):
        c.service.activate(stale, c.approval(stale))
    assert c.repository.revision('strategy') == 1


def test_rollback_records_requested_revision_even_when_artifact_repeats(release_case):
    c = release_case
    c.permitted = True
    for _ in range(2):
        candidate = c.service.propose(c.change())
        record = c.service.activate(candidate, c.approval(candidate))
        assert record.rollback_revision is None
    rollback = c.service.propose_rollback('strategy', 1, evidence())
    record = c.service.activate(rollback, c.approval(rollback))
    assert record.revision == 3 and record.rollback_revision == 1


@pytest.mark.parametrize('failure', ['tamper', 'actor', 'authorization', 'environment', 'future', 'scope', 'evidence'])
def test_release_rechecks_bound_authority(release_case, failure):
    c = release_case
    candidate = c.service.propose(c.change())
    approval = c.approval(candidate)
    c.permitted = True
    if failure == 'tamper':
        candidate = replace(candidate, change=replace(candidate.change, artifact=bundle(b'evil')[0]))
    elif failure == 'actor':
        approval = replace(approval, actor=replace(approval.actor, identity_id=''))
    elif failure == 'authorization':
        approval = replace(approval, authorization_id='other')
    elif failure == 'environment':
        approval = replace(approval, actor=replace(approval.actor, environment=t.Environment.LIVE))
    elif failure == 'future':
        approval = replace(approval, approved_at=LATER)
    elif failure == 'scope':
        approval = replace(approval, actor=replace(approval.actor, scopes=()))
    else:
        c.evidence_valid = False
    with pytest.raises(ReleaseDenied):
        c.service.activate(candidate, approval)
    assert c.repository.revision('strategy') == 0


def test_missing_review_or_evaluation_cannot_release(release_case):
    c = release_case
    c.evidence_valid = False
    with pytest.raises(ReleaseDenied):
        c.service.propose(c.change())
    c.evidence_valid = True
    c.service._evaluate = lambda change: ()
    with pytest.raises(ReleaseDenied, match='EVALUATION_REQUIRED'):
        c.service.propose(c.change())
    assert c.repository.history('strategy') == ()


def test_live_release_disabled(release_case):
    with pytest.raises(ReleaseDenied, match='LIVE_DISABLED'):
        ReleaseService(repository=release_case.repository, environment=t.Environment.LIVE,
            now=lambda: NOW, evaluate=lambda change: (), authorize=lambda c, a: True,
            verify_evidence=lambda ref, change, purpose: True)


@pytest.mark.parametrize('field,value', [('identity_id', 'model'), ('policy_revision', 2),
    ('ownership_epoch', 2), ('policy_revision', True), ('ownership_epoch', 0),
    ('scopes', []), ('scopes', (replace(rule_input().snapshot.scope, venue_account_id='other'),))])
def test_release_rejects_wrong_human_policy_epoch_or_account(release_case, field, value):
    c = release_case
    candidate = c.service.propose(c.change())
    approval = c.approval(candidate)
    c.permitted = True
    with pytest.raises(ReleaseDenied):
        c.service.activate(candidate, replace(approval, actor=replace(approval.actor, **{field: value})))
    assert c.repository.history('strategy') == ()


def test_review_and_evaluation_bind_exact_change_and_recheck_at_activation(release_case):
    c = release_case
    change = c.change()
    candidate = c.service.propose(change)
    assert [(item[1], item[2]) for item in c.evidence_checks] == [
        (change, 'REVIEW'), (change, 'EVALUATION')]
    c.permitted = True
    # Revocation after proposal prevents activation without consuming the candidate.
    c.evidence_valid = False
    with pytest.raises(ReleaseDenied, match='EVIDENCE'):
        c.service.activate(candidate, c.approval(candidate))
    assert c.repository.revision('strategy') == 0
    c.evidence_valid = True
    c.service.activate(candidate, c.approval(candidate))
    assert c.evidence_checks[-2:] == [(evidence(), change, 'REVIEW'), (evidence(), change, 'EVALUATION')]


def test_existing_review_for_different_artifact_is_not_a_review(release_case):
    c = release_case
    reviewed = c.change()
    c.service._verify_evidence = lambda ref, change, purpose: change == reviewed
    with pytest.raises(ReleaseDenied, match='EVIDENCE'):
        c.service.propose(c.change(b'unreviewed artifact'))
    assert c.repository.history('strategy') == ()


def test_release_authorizer_must_return_true_not_truthy(release_case):
    c = release_case
    candidate = c.service.propose(c.change())
    c.service._authorize = lambda candidate, approval: 'approved'
    with pytest.raises(ReleaseDenied, match='PERMISSION'):
        c.service.activate(candidate, c.approval(candidate))


def test_evaluation_reference_stream_is_bounded(release_case):
    c = release_case
    c.service._evaluate = lambda change: (evidence() for _ in range(33))
    with pytest.raises(ReleaseDenied, match='EVALUATION_LIMIT'):
        c.service.propose(c.change())
    assert c.repository.revision('strategy') == 0


def test_racing_evaluation_cannot_save_a_stale_candidate(release_case):
    c = release_case
    first = c.service.propose(c.change())
    c.permitted = True

    def evaluate(change):
        c.service.activate(first, c.approval(first))
        return (evidence(),)

    c.service._evaluate = evaluate
    with pytest.raises(ReleaseDenied, match='STALE_REVISION'):
        c.service.propose(c.change(b'late evaluation'))
    assert c.repository.history('strategy')[0].candidate == first


def test_concurrent_activation_consumes_one_revision(release_case):
    # Real threads and the production offline CAS; not PostgreSQL evidence.
    c = release_case
    candidates = (c.service.propose(c.change()), c.service.propose(c.change(b'competing')))
    c.permitted = True
    barrier = Barrier(2)

    def activate(candidate):
        barrier.wait(timeout=5)
        try:
            return c.service.activate(candidate, c.approval(candidate))
        except ReleaseDenied as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(activate, candidates))
    assert sum(isinstance(result, t.DeploymentRevision) for result in results) == 1
    assert sum(isinstance(result, ReleaseDenied) for result in results) == 1
    assert c.repository.revision('strategy') == 1


def test_release_does_not_move_history_between_deployments(release_case):
    c = release_case
    c.permitted = True
    first = c.service.propose(c.change())
    c.service.activate(first, c.approval(first))
    second = c.service.propose(c.change(b'second'))
    approval = c.approval(second)
    with pytest.raises(ReleaseDenied, match='DEPLOYMENT_MISMATCH'):
        c.service.activate(second, replace(approval, actor=replace(approval.actor, deployment_id='other')))
    assert c.repository.revision('strategy') == 1


def test_environment_bound_repository(release_case):
    with pytest.raises(ReleaseDenied, match='REPOSITORY_ENVIRONMENT'):
        ReleaseService(repository=release_case.repository, environment=t.Environment.PAPER,
            now=lambda: NOW, evaluate=lambda change: (evidence(),),
            authorize=lambda candidate, approval: True,
            verify_evidence=lambda ref, change, purpose: True)
    with pytest.raises(ReleaseDenied, match='LIVE_DISABLED'):
        OfflineReleaseRepository(environment=t.Environment.LIVE)


@pytest.mark.parametrize('revision', [0, -1, True, 999])
def test_unknown_rollback_never_creates_revision(release_case, revision):
    with pytest.raises(ReleaseDenied, match='ROLLBACK_REVISION_UNKNOWN'):
        release_case.service.propose_rollback('strategy', revision, evidence())
    assert release_case.repository.revision('strategy') == 0


def test_mutable_or_empty_evaluation_refs_cannot_be_activated(release_case):
    c = release_case
    candidate = c.service.propose(c.change())
    c.permitted = True
    for refs in ([], (), [evidence()]):
        with pytest.raises(ReleaseDenied):
            c.service.activate(replace(candidate, evaluation_refs=refs), c.approval(candidate))
    assert c.repository.revision('strategy') == 0


def memory_entry(**changes):
    return replace(t.MemoryEntry(id='memory-1', environment=t.Environment.RESEARCH,
        classification='FACT', text='synthetic observation', evidence=(evidence(),),
        visible_at=NOW, expires_at=LATER), **changes)


def test_memory_fact_inference_expiry_and_append_only():
    store = MemoryStore(environment=t.Environment.RESEARCH, now=lambda: LATER,
                        verify_evidence=lambda ref, entry: ref == evidence())
    entry = memory_entry()
    ref = store.append(entry)
    assert store.append(entry) == ref
    with pytest.raises(ValueError, match='ID_CONFLICT'):
        store.append(replace(entry, text='rewrite history'))
    inference = memory_entry(id='inference', classification='INFERENCE', evidence=(),
        text='Untrusted suggestion: raise capital', expires_at=None)
    store.append(inference)
    assert store.entries('FACT', as_of=NOW) == (entry,)
    assert store.entries('FACT', as_of=LATER) == ()
    assert store.entries('EXPIRED', as_of=LATER) == (replace(entry, classification='EXPIRED'),)
    assert store.entries('INFERENCE', as_of=LATER) == (inference,)
    assert store.append(entry) == ref  # expiry does not rewrite stored fact/hash


@pytest.mark.parametrize('changes', [{'evidence': ()}, {'classification': 'POLICY'},
    {'environment': t.Environment.LIVE}, {'expires_at': NOW}, {'visible_at': LATER},
    {'evidence': (replace(evidence(), hash='0' * 64),)}, {'evidence': [evidence()]},
    {'id': []}, {'text': ['mutable']}, {'text': 'x' * 16001},
    {'evidence': (replace(evidence(), visible_at=LATER),)},
    {'evidence': (replace(evidence(), environment=t.Environment.PAPER),)}])
def test_invalid_or_unverified_memory_rejected(changes):
    store = MemoryStore(environment=t.Environment.RESEARCH, now=lambda: NOW,
                        verify_evidence=lambda ref, entry: ref == evidence())
    with pytest.raises(ValueError):
        store.append(memory_entry(**changes))
    assert store.entries('FACT', as_of=NOW) == ()


def test_memory_as_of_visibility_and_explicit_expired_partition():
    store = MemoryStore(environment=t.Environment.RESEARCH, now=lambda: LATER,
                        verify_evidence=lambda ref, entry: True)
    future_entry = memory_entry(visible_at=LATER, expires_at=None)
    store.append(future_entry)
    expired = memory_entry(id='old', classification='EXPIRED', expires_at=None)
    store.append(expired)
    assert store.entries('FACT', as_of=NOW) == ()
    assert store.entries('FACT', as_of=LATER) == (future_entry,)
    assert store.entries('EXPIRED', as_of=NOW) == (expired,)
    with pytest.raises(ValueError, match='FUTURE_MEMORY_READ'):
        store.entries('FACT', as_of='2026-10-24T00:00:00Z')
    with pytest.raises(ValueError, match='INVALID_CLASSIFICATION'):
        store.entries('POLICY', as_of=NOW)
    with pytest.raises(ValueError, match='LIVE_DISABLED'):
        MemoryStore(environment=t.Environment.LIVE, now=lambda: NOW, verify_evidence=lambda ref, entry: True)


def test_memory_concurrent_rewrite_keeps_one_immutable_fact():
    store = MemoryStore(environment=t.Environment.RESEARCH, now=lambda: NOW,
                        verify_evidence=lambda ref, entry: ref == evidence())
    barrier = Barrier(2)

    def append(text):
        barrier.wait(timeout=5)
        try:
            return store.append(memory_entry(text=text))
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(append, ('first', 'second')))
    assert sum(isinstance(result, t.MemoryRef) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert len(store.entries('FACT', as_of=NOW)) == 1


def test_evidence_must_support_the_memory_content():
    expected = memory_entry()
    store = MemoryStore(environment=t.Environment.RESEARCH, now=lambda: NOW,
                        verify_evidence=lambda ref, entry: ref == evidence() and entry == expected)
    with pytest.raises(ValueError, match='MEMORY_EVIDENCE_INVALID'):
        store.append(replace(expected, text='unsupported inference promoted to fact'))
    assert store.entries('FACT', as_of=NOW) == ()
    assert store.append(expected).id == expected.id


def test_secret_and_socket_denied(sandbox_case, monkeypatch):
    monkeypatch.setenv('IP21_SYNTHETIC_HOST_SECRET', 'fake-test-only')
    result = sandbox_case.try_privileged_access()
    assert result.secret_reads == []
    assert result.signature_service_calls == []
    assert len(result.blocked_capabilities) == 7


def test_network_scope(sandbox_case):
    # All networking denied, including proxy/redirect destinations: no server
    # connection is needed or attempted when socket creation is denied.
    source = '''
import socket
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        s = socket.socket(family)
    except PermissionError:
        continue
    s.close()
    raise AssertionError('socket capability unexpectedly available')
print('{"action":"HOLD","proposal":null}')
'''
    service, artifact = sandbox_case.sandbox(source)
    assert service.run(artifact, rule_input(), limits()).action == 'HOLD'


def test_resource_limit_and_cancel(sandbox_case):
    # Positive control prevents a broken rootfs from masquerading as denial.
    good, artifact = sandbox_case.sandbox("print('{\"action\":\"HOLD\",\"proposal\":null}')")
    assert good.run(artifact, rule_input(), limits()).action == 'HOLD'
    service, artifact = sandbox_case.sandbox('while True: pass')
    start = time.monotonic()
    # The caller's owner/control loop is separate from the research worker.
    ticks = 0
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(service.run, artifact, rule_input(), limits(cpu_ms=1000))
        while not future.done() and time.monotonic() - start < 5:
            ticks += 1
            Event().wait(0.01)
        result = future.result(timeout=1)
    assert result.action == 'REJECT' and time.monotonic() - start < 5
    assert result.reasons[0] in ('TIME_LIMIT', 'WORKER_FAILED_OR_RESOURCE_LIMIT')
    assert ticks > 0
    cancel = Event()
    service, artifact = sandbox_case.sandbox('while True: pass', cancel)
    timer = Timer(0.2, cancel.set)
    timer.start()
    try:
        result = service.run(artifact, rule_input(), limits(cpu_ms=3000))
        assert result.reasons == ('CANCELLED',)
    finally:
        timer.cancel()
        timer.join()


def test_native_memory_limit_is_observed_inside_worker(sandbox_case):
    source = '''
import json
try:
    allocation = bytearray(512 * 1024**2)
except MemoryError:
    print('{"action":"HOLD","proposal":null}')
else:
    raise AssertionError('memory allocation unexpectedly succeeded')
'''
    service, artifact = sandbox_case.sandbox(source)
    # HOLD requires the real child to start, catch MemoryError and emit protocol.
    assert service.run(artifact, rule_input(), limits(memory_bytes=128 * 1024**2)).action == 'HOLD'


def test_native_output_limit_stops_writer(sandbox_case):
    good, artifact = sandbox_case.sandbox("print('{\"action\":\"HOLD\",\"proposal\":null}')")
    assert good.run(artifact, rule_input(), limits()).action == 'HOLD'
    _, content = bundle(b"import os\nwhile True: os.write(1, b'x' * 65536)", sandbox_case.lock)
    with pytest.raises(SandboxDenied, match='WORKER_FAILED_OR_RESOURCE_LIMIT|OUTPUT_LIMIT'):
        LinuxRunner(rootfs=sandbox_case.rootfs).run(content, b'{}', limits())


def test_native_filesystem_readonly_and_tmp_bounded(sandbox_case):
    source = '''
import errno
for path in ('/job/rule.py', '/dependency.lock', '/dev/new-file'):
    try:
        with open(path, 'wb') as stream:
            stream.write(b'bad')
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM)
    else:
        raise AssertionError('read-only mount was writable')
try:
    for number in range(40):
        with open('/tmp/item-' + str(number), 'wb') as stream:
            stream.write(b'x' * 60000)
except OSError as exc:
    assert exc.errno == errno.ENOSPC
else:
    raise AssertionError('tmpfs size limit not enforced')
print('{"action":"HOLD","proposal":null}')
'''
    service, artifact = sandbox_case.sandbox(source)
    assert service.run(artifact, rule_input(), limits()).action == 'HOLD'


def test_native_process_creation_denied(sandbox_case):
    source = '''
import os
try:
    os.fork()
except PermissionError:
    print('{"action":"HOLD","proposal":null}')
else:
    raise AssertionError('fork allowed')
'''
    service, artifact = sandbox_case.sandbox(source)
    assert service.run(artifact, rule_input(), limits()).action == 'HOLD'
