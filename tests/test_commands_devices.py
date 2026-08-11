"""Device commands against the mock."""

import pytest

from control_surface.registry import LiveAPIError
from tests.conftest import run_command
from tests.mock_live import MockDevice


@pytest.fixture()
def with_device(song):
    song.tracks[0].devices.append(MockDevice(name="Drift", class_name="InstrumentVector"))
    return song.tracks[0].devices[0]


def test_get_devices_list(registry, ctx, song, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0)
    assert result["devices"][0]["name"] == "Drift"
    assert result["devices"][0]["parameter_count"] == 3


def test_get_device_parameters(registry, ctx, song, with_device):
    result = run_command(registry, ctx, "get_devices", track_index=0, device_index=0)
    params = result["device"]["parameters"]
    assert params[0]["name"] == "Device On"
    assert all(0.0 <= p["value"] <= 1.0 for p in params)
    assert all("display_value" in p for p in params)


def test_set_parameters_by_name_and_index(registry, ctx, song, with_device):
    result = run_command(
        registry,
        ctx,
        "set_device_parameters",
        track_index=0,
        device_index=0,
        parameters=[
            {"parameter": "macro 1", "value": 0.9},
            {"parameter": 2, "value": 0.1},
        ],
    )
    assert abs(with_device.parameters[1].value - 0.9) < 1e-9
    assert abs(with_device.parameters[2].value - 0.1) < 1e-9
    assert len(result["changed"]) == 2


def test_set_parameters_unknown_name_lists_available(registry, ctx, song, with_device):
    with pytest.raises(LiveAPIError, match="Available"):
        run_command(
            registry,
            ctx,
            "set_device_parameters",
            track_index=0,
            device_index=0,
            parameters=[{"parameter": "Filter Cutoff", "value": 0.5}],
        )


def test_bypass_device(registry, ctx, song, with_device):
    result = run_command(
        registry, ctx, "set_device_parameters", track_index=0, device_index=0, enabled=False
    )
    assert with_device.is_active is False
    assert result["is_active"] is False


def test_delete_device(registry, ctx, song, with_device):
    result = run_command(registry, ctx, "delete_device", track_index=0, device_index=0)
    assert result == {"deleted": "Drift", "device_count": 0}
    assert song.tracks[0].devices == []


def test_device_out_of_range(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="out of range"):
        run_command(registry, ctx, "get_devices", track_index=0, device_index=5)
