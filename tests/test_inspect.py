"""inspect (2.19): read-only views of any Live object by canonical path."""

import pytest

from control_surface.registry import LiveAPIError
from tests.helpers import run_command
from tests.mock_live import MockRackDevice, MockSimplerDevice


def _inspect(registry, ctx, path, **kw):
    return run_command(registry, ctx, "inspect", path=path, **kw)


def test_song_level_properties_and_children(registry, ctx, song):
    result = _inspect(registry, ctx, "live_set")
    assert result["path"] == "live_set"
    assert result["properties"]["tempo"] == 120.0
    assert result["children"]["tracks"] == {"list": 2, "of": "MockTrack"}


def test_walk_into_a_track_and_its_mixer(registry, ctx, song):
    track = _inspect(registry, ctx, "live_set tracks 1")
    assert track["properties"]["name"] == "2 Track"
    assert "mixer_device" in track["children"]
    volume = _inspect(registry, ctx, "live_set tracks 1 mixer_device volume")
    assert volume["properties"]["name"] == "Track Volume"


def test_list_paths_list_their_items(registry, ctx, song):
    result = _inspect(registry, ctx, "live_set scenes")
    assert result["list"] == 4


def test_selected_properties_only(registry, ctx, song):
    result = _inspect(registry, ctx, "live_set", properties=["tempo", "is_playing"])
    assert result["properties"] == {"tempo": 120.0, "is_playing": False}


def test_deep_rack_path(registry, ctx, song):
    kit = MockRackDevice(name="Kit", drum=True)
    kit.fill_pad(36, "Kick", [MockSimplerDevice("Kick")])
    song.tracks[0].devices.append(kit)
    result = _inspect(registry, ctx, "live_set tracks 0 devices 0 drum_pads 36 chains 0 devices 0")
    assert result["properties"]["playback_mode"] == 0


def test_unreadable_properties_are_reported_not_fatal(registry, ctx, song):
    # Live raises when value_items is read on a continuous parameter.
    result = _inspect(registry, ctx, "live_set tracks 0 mixer_device volume")
    assert "value_items" in result["unreadable"]


def test_methods_are_never_called(registry, ctx, song):
    with pytest.raises(LiveAPIError, match="is a method"):
        _inspect(registry, ctx, "live_set stop_playing")
    assert song.is_playing is False


@pytest.mark.parametrize(
    "path, message",
    [
        ("tracks 0", "Paths start with"),
        ("live_set nope", "has no 'nope'"),
        ("live_set tracks 9", "out of range"),
        ("live_set tracks name", "is a list"),
        ("live_set tracks 0 _private", "can't be inspected"),
    ],
)
def test_bad_paths_explain_themselves(registry, ctx, path, message):
    with pytest.raises(LiveAPIError, match=message):
        _inspect(registry, ctx, path)


def test_inspect_is_read_only():
    from control_surface.commands import REGISTRY

    schema = REGISTRY.get("inspect")
    assert schema.read_only is True and schema.internal is False
