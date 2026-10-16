"""Undo grouping (one write call = one Live undo step) and the undo tool,
driven through the socket server so the grouping wrapper is in the path."""

import socket

import pytest

from control_surface.commands import REGISTRY
from control_surface.socket_server import SocketServer
from tests.helpers import read_frame, write_frame
from tests.mock_live import MockControlSurface, MockSong


@pytest.fixture()
def song():
    return MockSong(track_count=2, scene_count=4, return_count=2)


@pytest.fixture()
def call(song):
    srv = SocketServer(
        MockControlSurface(song), host="127.0.0.1", port=0, use_tcp=True, registry=REGISTRY
    )
    srv.start()
    sock = socket.create_connection(("127.0.0.1", srv.bound_port), timeout=5)
    counter = iter(range(10_000))

    def send(command, **params):
        write_frame(sock, {"type": command, "params": params, "id": f"r{next(counter)}"})
        return read_frame(sock)

    yield send
    sock.close()
    srv.stop()


def test_a_batched_write_is_one_undo_step(call, song):
    response = call("set_track", track_index=0, name="Bass", volume=0.5, pan=0.3, mute=True)
    assert response["status"] == "success"
    assert song.undo_step_log == ["begin", "end"]
    assert song.can_undo


def test_reads_open_no_undo_step(call, song):
    assert call("get_session_overview")["status"] == "success"
    assert call("get_transport_state")["status"] == "success"
    assert song.undo_step_log == []


def test_a_failing_write_still_closes_its_undo_step(call, song):
    response = call("delete_track", track_index=99)
    assert response["status"] == "error"
    assert song.undo_step_log == ["begin", "end"]


def test_undo_and_redo(call, song):
    call("set_track", track_index=0, name="A")
    call("set_track", track_index=0, name="B")
    undone = call("undo", steps=5)["result"]
    assert undone == {"undone": 2, "can_undo": False, "can_redo": True}
    redone = call("undo", redo=True)["result"]
    assert redone == {"redone": 1, "can_undo": True, "can_redo": True}


def test_undo_does_not_open_its_own_step(call, song):
    call("set_track", track_index=0, name="A")
    song.undo_step_log.clear()
    call("undo")
    assert song.undo_step_log == []


def test_undo_with_nothing_to_undo(call, song):
    assert call("undo")["result"] == {"undone": 0, "can_undo": False, "can_redo": False}
