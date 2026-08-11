"""Thread marshaling: success, error propagation, typed re-raise, timeout,
deadline refusal, and late-outcome journaling."""

import threading
import time

import pytest

from control_surface.errors import LiveAPIError, PartialApplyError, ValidationError
from control_surface.thread_marshal import MainThreadExecutionError, ThreadMarshaler
from tests.helpers import ImmediateControlSurface


class DroppingControlSurface:
    """Accepts the task but never runs it — forces a marshal timeout."""

    def schedule_message(self, delay, callback):
        pass


class CapturingControlSurface:
    """Stores the scheduled task without running it — tests fire it when they
    choose, simulating Live's main thread waking up late."""

    def __init__(self):
        self.callback = None

    def schedule_message(self, delay, callback):
        self.callback = callback


class FakeOperationLogger:
    def __init__(self):
        self.events = []

    def log_marshal_event(self, kind, command, detail):
        self.events.append((kind, command, detail))


class BrokenControlSurface:
    def schedule_message(self, delay, callback):
        raise RuntimeError("scheduler unavailable")


def test_execute_success():
    marshaler = ThreadMarshaler(ImmediateControlSurface())
    assert marshaler.execute(lambda a, b: a + b, 2, 3) == 5


def test_execute_kwargs():
    marshaler = ThreadMarshaler(ImmediateControlSurface())
    assert marshaler.execute(lambda x=0: x * 2, x=21) == 42


def test_generic_error_wrapped():
    marshaler = ThreadMarshaler(ImmediateControlSurface())

    def boom():
        raise RuntimeError("kaput")

    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(boom)
    assert "kaput" in str(exc.value)
    assert exc.value.timeout is False


def test_live_api_error_survives_thread_hop():
    marshaler = ThreadMarshaler(ImmediateControlSurface())

    def missing_track():
        raise LiveAPIError("Track 99 does not exist")

    with pytest.raises(LiveAPIError):
        marshaler.execute(missing_track)


def test_validation_error_survives_thread_hop():
    marshaler = ThreadMarshaler(ImmediateControlSurface())

    def invalid():
        raise ValidationError("bad value", param="x")

    with pytest.raises(ValidationError):
        marshaler.execute(invalid)


def test_partial_apply_error_survives_hop_with_attributes():
    # The original exception object must cross the hop, not a rebuilt copy:
    # PartialApplyError's .applied is what tells the caller which writes landed.
    marshaler = ThreadMarshaler(ImmediateControlSurface())

    def partial():
        raise PartialApplyError("arm", "cannot arm this track", applied=["name", "volume"])

    with pytest.raises(PartialApplyError) as exc:
        marshaler.execute(partial)
    assert isinstance(exc.value, LiveAPIError)
    assert exc.value.applied == ["name", "volume"]
    assert exc.value.failed_field == "arm"
    assert "Already applied: name, volume" in str(exc.value)


def test_timeout():
    marshaler = ThreadMarshaler(DroppingControlSurface(), default_timeout=0.05, grace=0.05)
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None)
    assert exc.value.timeout is True
    # Deadline refusal makes this promise safe to state:
    assert "NOT modified" in str(exc.value)


def test_timeout_override():
    marshaler = ThreadMarshaler(DroppingControlSurface(), default_timeout=100, grace=0.05)
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None, timeout=0.05)
    assert exc.value.timeout is True


def test_expired_task_refuses_to_run_after_abandonment():
    # Timeline: request times out and is abandoned; Live's main thread then
    # wakes up and runs the scheduled task. It must refuse to execute the
    # handler and journal 'expired'.
    surface = CapturingControlSurface()
    journal = FakeOperationLogger()
    marshaler = ThreadMarshaler(surface, default_timeout=0.05, grace=0.05, operation_logger=journal)
    ran = []
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: ran.append(1), command="poke")
    assert exc.value.timeout is True

    surface.callback()  # Live wakes up late
    assert ran == []  # the handler never executed — the Set was not modified
    assert len(journal.events) == 1
    kind, command, _detail = journal.events[0]
    assert (kind, command) == ("expired", "poke")


def test_expired_task_within_grace_reports_never_executed():
    # The task STARTS after its deadline but while the waiter is still inside
    # the grace window: the waiter receives the refusal as an informative
    # typed timeout, and the handler never ran.
    surface = CapturingControlSurface()
    marshaler = ThreadMarshaler(surface, default_timeout=0.2, grace=1.0)
    ran = []

    def fire_late():
        time.sleep(0.5)  # past the 0.2s deadline, well inside 0.2+1.0 grace
        surface.callback()

    t = threading.Thread(target=fire_late)
    t.start()
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: ran.append(1))
    t.join()
    assert ran == []
    assert exc.value.timeout is True
    assert "refused to run" in str(exc.value)


def test_late_finish_after_abandon_is_journaled():
    # Residual race: the task starts BEFORE its deadline but finishes after
    # the waiter abandoned the request. The Set changed despite the timeout
    # error — the journal is the only record, so it must exist.
    surface = CapturingControlSurface()
    journal = FakeOperationLogger()
    marshaler = ThreadMarshaler(surface, default_timeout=0.3, grace=0.1, operation_logger=journal)

    def slow():
        time.sleep(0.8)  # finishes well after timeout+grace (0.4s)
        return "done"

    def fire_soon():
        time.sleep(0.05)  # starts well before the 0.3s deadline
        surface.callback()

    t = threading.Thread(target=fire_soon)
    t.start()
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(slow, command="slowpoke")
    assert exc.value.timeout is True
    t.join()  # waits for slow() to finish and the task to journal
    assert len(journal.events) == 1
    kind, command, _detail = journal.events[0]
    assert (kind, command) == ("late_success", "slowpoke")


def test_schedule_failure():
    marshaler = ThreadMarshaler(BrokenControlSurface())
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None)
    assert "Failed to schedule" in str(exc.value)
    assert exc.value.timeout is False
