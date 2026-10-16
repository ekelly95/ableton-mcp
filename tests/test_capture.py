"""capture_audio: the server drives the Live-side capture_* steps (each its
own command) through a real socket server against the mock song."""

import pytest

from control_surface.commands import REGISTRY
from control_surface.socket_server import SocketServer
from mcp_server.client import AbletonClient, CommandError
from mcp_server.server import _capture_audio
from tests.mock_live import MockClipSlot, MockControlSurface, MockSong
from tests.test_audio_analysis import sine, write_wav


@pytest.fixture()
def song():
    return MockSong(track_count=2, scene_count=4, return_count=2)


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


@pytest.fixture()
def recording(tmp_path, monkeypatch):
    path = write_wav(tmp_path / "Capture.wav", sine(1000, -23.0, seconds=5.0), 48000)
    monkeypatch.setattr(MockClipSlot, "capture_file", path)
    return path


def test_capture_records_analyzes_and_cleans_up(client, song, recording):
    tracks_before = len(song.tracks)
    result = _capture_audio(client, {"length_beats": 8}, sleep=lambda s: None)
    assert result["length_beats"] == 8
    assert result["analysis"]["integrated_lufs"] == pytest.approx(-23.0, abs=0.1)
    assert len(song.tracks) == tracks_before  # the temporary track is gone
    assert song.is_playing is False


def test_capture_track_is_routed_safely(client, song, recording, monkeypatch):
    seen = {}
    original = MockClipSlot.fire

    def spy(slot, *args, **kwargs):
        track = slot._track
        seen.update(
            input=track.input_routing_type.display_name,
            monitoring=track.current_monitoring_state,
            armed=track.arm,
            kwargs=kwargs,
        )
        return original(slot, *args, **kwargs)

    monkeypatch.setattr(MockClipSlot, "fire", spy)
    _capture_audio(client, {"length_beats": 4, "analyze": False}, sleep=lambda s: None)
    assert seen == {
        "input": "Resampling",
        "monitoring": 2,  # off: no feedback through the capture track
        "armed": True,
        "kwargs": {"record_length": 4.0, "launch_quantization": 0},
    }


def test_capture_refuses_while_playing_and_leaves_no_track(client, song, recording):
    song.is_playing = True
    tracks_before = len(song.tracks)
    with pytest.raises(CommandError, match="Stop playback first"):
        _capture_audio(client, {"length_beats": 4}, sleep=lambda s: None)
    assert len(song.tracks) == tracks_before


def test_capture_cleans_up_even_when_a_step_fails(client, song, recording, monkeypatch):
    tracks_before = len(song.tracks)

    def broken(slot, *args, **kwargs):
        raise RuntimeError("audio engine is off")

    monkeypatch.setattr(MockClipSlot, "fire", broken)
    with pytest.raises(CommandError, match="audio engine is off"):
        _capture_audio(client, {"length_beats": 4}, sleep=lambda s: None)
    assert len(song.tracks) == tracks_before


def test_capture_steps_are_not_mcp_tools():
    tools = {t["name"] for t in REGISTRY.generate_mcp_tools()}
    assert not tools & {
        "capture_prepare",
        "capture_arm",
        "capture_record",
        "capture_status",
        "capture_finish",
        "capture_cleanup",
    }


def test_recording_is_deleted_after_analysis_unless_kept(client, song, recording):
    import os

    with open(recording + ".asd", "wb") as f:
        f.write(b"live analysis sidecar")
    kept = _capture_audio(client, {"length_beats": 4, "keep_recording": True}, sleep=lambda s: None)
    assert kept["file_path"] == recording and os.path.exists(recording)
    gone = _capture_audio(client, {"length_beats": 4}, sleep=lambda s: None)
    assert "file_path" not in gone
    assert gone["analysis"]["integrated_lufs"] is not None
    assert not os.path.exists(recording) and not os.path.exists(recording + ".asd")
