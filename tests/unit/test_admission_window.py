from datetime import datetime, timedelta, timezone

from trading_system.risk.admission_clock import AdmissionWindow


START=datetime(2026,10,29,tzinfo=timezone.utc)


def window(wall,motion):
    walls=iter((START+timedelta(microseconds=n)).isoformat() for n in wall)
    ticks=iter(motion)
    return AdmissionWindow(started_at=START,started_ns=1000,
        wall_clock=lambda:next(walls),monotonic_clock=lambda:next(ticks))


def test_fresh_poll_requires_both_independent_clocks():
    value=window([5],[6000]).poll(START+timedelta(microseconds=10))
    assert value[1]==START+timedelta(microseconds=5) and value[2]==6000


def test_wall_expiry_cannot_be_overridden_by_slow_monotonic_clock():
    assert window([10],[1001]).poll(START+timedelta(microseconds=10)) is None


def test_monotonic_expiry_cannot_be_overridden_by_slow_wall_clock():
    assert window([1],[11000]).poll(START+timedelta(microseconds=10)) is None


def test_backwards_wall_step_denies_after_a_valid_poll():
    clock=window([5,4],[6000,7000]);until=START+timedelta(microseconds=10)
    assert clock.poll(until) is not None
    assert clock.poll(until) is None


def test_backwards_monotonic_step_denies_after_a_valid_poll():
    clock=window([4,5],[6000,5000]);until=START+timedelta(microseconds=10)
    assert clock.poll(until) is not None
    assert clock.poll(until) is None


def test_risk_deadline_can_narrow_but_never_extend_an_existing_window():
    clock=window([4,7],[5000,8000])
    assert clock.poll(START+timedelta(microseconds=6)) is not None
    assert clock.poll(START+timedelta(seconds=30)) is None


def test_shorter_final_quote_deadline_is_enforced():
    clock=window([4,7],[5000,8000])
    assert clock.poll(START+timedelta(seconds=30)) is not None
    assert clock.poll(START+timedelta(microseconds=6)) is None


def test_boolean_is_not_a_monotonic_clock_reading():
    assert window([1],[True]).poll(START+timedelta(seconds=30)) is None
