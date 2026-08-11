"""Transport commands against the mock song."""

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.conftest import run_command


def test_get_transport_state(registry, ctx, song):
    state = run_command(registry, ctx, "get_transport_state")
    assert state["is_playing"] is False
    assert state["tempo"] == 120.0
    assert state["loop"] == {"enabled": False, "start": 0.0, "length": 4.0}


def test_play_stop_continue(registry, ctx, song):
    state = run_command(registry, ctx, "transport_control", action="play")
    assert state["is_playing"] is True
    state = run_command(registry, ctx, "transport_control", action="stop")
    assert state["is_playing"] is False
    state = run_command(registry, ctx, "transport_control", action="continue")
    assert state["is_playing"] is True


def test_play_with_position_is_two_phase(registry, ctx, song):
    # Real Live applies playhead seeks only between scheduled tasks, so a
    # seek-then-play must span two requests (the MCP server loops on
    # "seeking" transparently).
    first = run_command(registry, ctx, "transport_control", action="play", position=16.0)
    assert first == {
        "phase": "seeking",
        "note": first["note"],
    }
    assert song.is_playing is False  # phase 1 must NOT start playback

    second = run_command(registry, ctx, "transport_control", action="play", position=16.0)
    assert second["is_playing"] is True
    assert second["current_song_time"] == 16.0
    assert song.current_song_time == 16.0


def test_play_from_current_position_is_single_phase(registry, ctx, song):
    state = run_command(registry, ctx, "transport_control", action="play", position=0.0)
    assert "phase" not in state
    assert state["is_playing"] is True


def test_seek_while_playing_is_single_response(registry, ctx, song):
    run_command(registry, ctx, "transport_control", action="play")
    state = run_command(registry, ctx, "transport_control", action="play", position=32.0)
    assert "phase" not in state
    assert state["requested_position"] == 32.0
    assert state["is_playing"] is True
    # The seek itself still lands at the next task boundary, not in-task.
    assert state["current_song_time"] == 0.0
    run_command(registry, ctx, "get_transport_state")
    assert song.current_song_time == 32.0


def test_stop_with_position_parks_then_confirms(registry, ctx, song):
    run_command(registry, ctx, "transport_control", action="play")
    first = run_command(registry, ctx, "transport_control", action="stop", position=8.0)
    assert first["phase"] == "seeking"
    assert song.is_playing is False  # phase 1 already stopped the transport
    second = run_command(registry, ctx, "transport_control", action="stop", position=8.0)
    assert second["is_playing"] is False
    assert second["current_song_time"] == 8.0


def test_set_transport_batch(registry, ctx, song):
    state = run_command(
        registry,
        ctx,
        "set_transport",
        tempo=140.0,
        signature_numerator=3,
        signature_denominator=4,
        loop_enabled=True,
        loop_start=4.0,
        loop_length=8.0,
        metronome=True,
    )
    assert state["tempo"] == 140.0
    assert state["signature_numerator"] == 3
    assert state["metronome"] is True
    assert state["loop"] == {"enabled": True, "start": 4.0, "length": 8.0}
    assert song.tempo == 140.0


def test_set_transport_partial_leaves_rest(registry, ctx, song):
    run_command(registry, ctx, "set_transport", tempo=99.0)
    assert song.tempo == 99.0
    assert song.signature_numerator == 4
    assert song.metronome is False


def test_invalid_scale_name_leaves_everything_untouched(registry, ctx, song):
    # scale_name is the only Live-validated field; it writes FIRST so its
    # failure is atomic (tempo used to land before the scale check raised).
    with pytest.raises(LiveAPIError, match="rejected scale name"):
        run_command(registry, ctx, "set_transport", tempo=140.0, scale_name="Klingon Blues")
    assert song.tempo == 120.0
    assert song.scale_name == "Major"


def test_invalid_scale_root_rejected_before_any_write(registry, ctx, song):
    with pytest.raises(ValidationError):
        run_command(registry, ctx, "set_transport", tempo=140.0, scale_root="H")
    assert song.tempo == 120.0
