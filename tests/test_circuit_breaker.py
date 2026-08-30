"""Tests for the CircuitBreaker guarding poll updates."""

import threading
import time
from time import sleep

import pytest

from dbus_mqtt_battery.dbus_utils import CircuitBreaker, create_poll_function


def make_breaker(**kwargs):
    """Build a CircuitBreaker with fast test timings."""
    defaults = {"name": "test", "call_timeout_s": 0.1, "failure_threshold": 2, "cooldown_s": 0.2}
    defaults.update(kwargs)
    return CircuitBreaker(**defaults)


def test_success_passes_through():
    """Closed breaker runs the call and stays closed."""
    breaker = make_breaker()
    calls = []

    assert breaker.call(lambda: calls.append(1)) is True
    assert calls == [1]
    assert breaker.state == "closed"


def test_timeout_counts_as_failure():
    """A call exceeding the timeout is recorded as a failure but keeps calling."""
    breaker = make_breaker()

    def hang():
        sleep(0.3)

    assert breaker.call(hang) is False
    assert breaker.consecutive_failures == 1
    assert breaker.state == "closed"  # below threshold, still calling


def test_breaker_opens_after_threshold_and_skips_calls():
    """After threshold consecutive timeouts the breaker opens and skips calls."""
    breaker = make_breaker()

    def hang():
        sleep(0.3)

    breaker.call(hang)
    breaker.call(hang)
    assert breaker.state == "open"

    calls = []
    # Skipped while open - fn must not run
    assert breaker.call(lambda: calls.append(1)) is False
    assert not calls


def test_half_open_recovers_on_success():
    """After the cooldown a successful probe closes the breaker again."""
    breaker = make_breaker()

    def hang():
        sleep(0.3)

    breaker.call(hang)
    breaker.call(hang)
    sleep(0.25)  # past cooldown -> half-open
    assert breaker.state == "half-open"

    assert breaker.call(lambda: None) is True
    assert breaker.state == "closed"
    assert breaker.consecutive_failures == 0


def test_non_timeout_exception_propagates():
    """Non-timeout errors are not swallowed by the breaker."""
    breaker = make_breaker()

    def boom():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        breaker.call(boom)


def test_unguarded_off_main_thread():
    """Off-main-thread calls degrade to pass-through instead of crashing."""
    breaker = make_breaker(call_timeout_s=0.1)
    ran = []
    t = threading.Thread(target=lambda: breaker.call(lambda: ran.append(1)))
    t.start()
    t.join()
    assert ran == [1]


class FakeService:
    """Minimal service whose update() always fails."""

    def __init__(self):
        self.count = 0

    def update(self):
        """Always fail, to exercise the breaker failure path."""
        self.count += 1
        raise RuntimeError("update failed")


def test_poll_function_uses_breaker():
    """create_poll_function wires a default breaker around service.update()."""
    service = FakeService()
    poll = create_poll_function(service)

    start = time.time()
    poll()  # exception swallowed by poll, breaker records failure
    elapsed = time.time() - start
    assert service.count == 1
    # A hung update would have blocked ~10s (CALL_TIMEOUT_S); fast failure means no alarm fired
    assert elapsed < 1.0
