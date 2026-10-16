"""Stale-set refusal (2.18): index-addressed writes are refused when the set's
structure changed since the caller's last read. "User edits" below are made
straight on the mock song, behind the bridge's back."""

import pytest

from control_surface.commands import REGISTRY
from control_surface.socket_server import SocketServer
from mcp_server.client import AbletonClient, CommandError
from tests.mock_live import MockControlSurface, MockDevice, MockSong


@pytest.fixture()
def song():
    return MockSong(track_count=3, scene_count=4, return_count=2)


@pytest.fixture()
def client(song):
    srv = SocketServer(
        MockControlSurface(song), host="127.0.0.1", port=0, use_tcp=True, registry=REGISTRY
    )
    srv.start()
    c = AbletonClient(port=srv.bound_port, use_tcp=True, timeout=10)
    yield c
    c.close()
    srv.stop()


def test_own_writes_never_trip_the_check(client, song):
    client.send("get_session_overview")
    client.send("set_track", track_index=0, name="Bass")
    client.send("create_track", type="midi")
    client.send("set_track", track_index=3, name="Lead")
    assert [t.name for t in song.tracks][3] == "Lead"


def test_write_after_a_user_edit_is_refused_and_changes_nothing(client, song):
    client.send("get_session_overview")
    song.create_midi_track(0)  # the user inserts a track at the top
    with pytest.raises(CommandError) as refused:
        client.send("set_track", track_index=1, name="Bass")
    assert refused.value.error_type == "StaleSetError"
    assert "re-read" in str(refused.value)
    assert "Bass" not in [t.name for t in song.tracks]


def test_a_refusal_holds_until_the_caller_reads_again(client, song):
    client.send("get_session_overview")
    song.create_midi_track(0)
    for _ in range(2):  # a blind retry is refused too
        with pytest.raises(CommandError, match="changed since you last read"):
            client.send("set_track", track_index=1, name="Bass")
    client.send("get_session_overview")
    client.send("set_track", track_index=1, name="Bass")
    assert song.tracks[1].name == "Bass"


@pytest.mark.parametrize(
    "user_edit",
    [
        lambda s: s.delete_track(1),
        lambda s: setattr(s.tracks[2], "name", "Renamed by user"),
        lambda s: s.tracks[0].devices.append(MockDevice("Reverb", "Reverb")),
        lambda s: s.tracks[1].clip_slots[0].create_clip(4.0),
        lambda s: s.create_scene(-1),
    ],
    ids=["delete track", "rename track", "add device", "add clip", "add scene"],
)
def test_structural_edits_are_caught(client, song, user_edit):
    client.send("get_session_overview")
    user_edit(song)
    with pytest.raises(CommandError, match="changed since you last read"):
        client.send("set_track", track_index=0, mute=True)


def test_a_tweaked_knob_is_not_a_structural_change(client, song):
    client.send("get_session_overview")
    song.tracks[0].mixer_device.volume.value = 0.3  # the user moves a fader
    client.send("set_track", track_index=0, name="Still fine")


def test_writes_without_positions_skip_the_check(client, song):
    client.send("get_session_overview")
    song.create_midi_track(0)
    client.send("set_transport", tempo=97.0)
    assert song.tempo == 97.0


def test_undo_is_refused_after_a_user_edit(client, song):
    client.send("get_session_overview")
    client.send("set_track", track_index=0, name="Mine")
    song.create_midi_track(0)
    with pytest.raises(CommandError, match="changed since you last read"):
        client.send("undo")


def test_no_check_before_the_first_read(client, song):
    song.create_midi_track(0)
    client.send("set_track", track_index=0, name="First call")


def test_the_version_never_reaches_tool_results(client):
    result = client.send("get_transport_state")
    assert "set_version" not in result
    assert client.set_version
