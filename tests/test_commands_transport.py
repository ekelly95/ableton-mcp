"""Transport commands against the mock song."""

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


def test_play_with_position(registry, ctx, song):
    state = run_command(registry, ctx, "transport_control", action="play", position=16.0)
    assert song.current_song_time == 16.0
    assert state["current_song_time"] == 16.0


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
