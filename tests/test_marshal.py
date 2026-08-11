"""Thread marshaling: success, error propagation, typed re-raise, timeout."""

import pytest

from control_surface.errors import PartialApplyError
from control_surface.registry import LiveAPIError, ValidationError
from control_surface.thread_marshal import MainThreadExecutionError, ThreadMarshaler


class ImmediateControlSurface:
    """Executes scheduled tasks synchronously, like tests want and unlike Live."""

    def schedule_message(self, delay, callback):
        callback()


class DroppingControlSurface:
    """Accepts the task but never runs it — forces a marshal timeout."""

    def schedule_message(self, delay, callback):
        pass


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
    marshaler = ThreadMarshaler(DroppingControlSurface(), default_timeout=0.05)
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None)
    assert exc.value.timeout is True


def test_timeout_override():
    marshaler = ThreadMarshaler(DroppingControlSurface(), default_timeout=100)
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None, timeout=0.05)
    assert exc.value.timeout is True


def test_schedule_failure():
    marshaler = ThreadMarshaler(BrokenControlSurface())
    with pytest.raises(MainThreadExecutionError) as exc:
        marshaler.execute(lambda: None)
    assert "Failed to schedule" in str(exc.value)
    assert exc.value.timeout is False
