"""Parameter normalization round-trips and edge cases."""

from dataclasses import dataclass

from control_surface.utils.normalize import denormalize_parameter, normalize_parameter


@dataclass
class FakeParam:
    value: float = 0.0
    min: float = 0.0
    max: float = 1.0


def test_round_trip():
    param = FakeParam(min=-12.0, max=12.0)
    for raw in (-12.0, -3.0, 0.0, 7.5, 12.0):
        normalized = normalize_parameter(param, raw)
        assert 0.0 <= normalized <= 1.0
        assert abs(denormalize_parameter(param, normalized) - raw) < 1e-9


def test_uses_current_value_when_none():
    param = FakeParam(value=0.5, min=0.0, max=1.0)
    assert normalize_parameter(param) == 0.5


def test_denormalize_clamps():
    param = FakeParam(min=0.0, max=10.0)
    assert denormalize_parameter(param, 1.5) == 10.0
    assert denormalize_parameter(param, -0.5) == 0.0


def test_zero_span_guard():
    param = FakeParam(value=5.0, min=5.0, max=5.0)
    assert normalize_parameter(param) == 0.0
