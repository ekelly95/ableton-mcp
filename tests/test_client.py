"""AbletonClient: framing, reconnect-once, and the no-resend-on-timeout rule."""

import socket
import threading

import pytest

from mcp_server.client import AbletonClient, AbletonConnectionError, CommandError
from tests.helpers import read_frame, write_frame


class ScriptedServer:
    """Tiny localhost server whose per-connection behaviour is scripted.

    behaviours: list of strings, one per accepted connection:
      "serve"         — answer requests normally until the client disconnects
      "drop"          — accept, then immediately close (simulates dead socket)
      "stall"         — accept, read the request, never answer
      "read_then_die" — read (and record) ONE request, then close without
                        replying: the request WAS delivered, response lost
    """

    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.requests_received = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        for behaviour in self.behaviours:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            if behaviour == "drop":
                conn.close()
                continue
            try:
                while True:
                    request = read_frame(conn)
                    if request is None:
                        break
                    self.requests_received.append(request)
                    if behaviour == "read_then_die":
                        break  # delivered but no response — connection dies
                    if behaviour == "stall":
                        continue  # swallow it, never reply
                    response = {
                        "status": "success",
                        "result": {"echo": request.get("type")},
                        "id": request.get("id"),
                    }
                    if request.get("type") == "explode":
                        response = {
                            "status": "error",
                            "error": "boom",
                            "error_type": "LiveAPIError",
                            "id": request.get("id"),
                        }
                    if request.get("type") == "partial":
                        response = {
                            "status": "error",
                            "error": "arm failed: nope. Already applied: name, volume.",
                            "error_type": "PartialApplyError",
                            "applied": ["name", "volume"],
                            "id": request.get("id"),
                        }
                    write_frame(conn, response)
            except OSError:
                pass
            finally:
                conn.close()
        self._sock.close()

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


def test_send_round_trip():
    server = ScriptedServer(["serve"])
    client = AbletonClient(port=server.port)
    try:
        assert client.send("hello") == {"echo": "hello"}
        assert client.send("again") == {"echo": "again"}
        assert len(server.requests_received) == 2
    finally:
        client.close()
        server.close()


def test_command_error_taxonomy():
    server = ScriptedServer(["serve"])
    client = AbletonClient(port=server.port)
    try:
        with pytest.raises(CommandError) as exc:
            client.send("explode")
        assert exc.value.error_type == "LiveAPIError"
    finally:
        client.close()
        server.close()


def test_partial_apply_error_reaches_caller_with_applied():
    server = ScriptedServer(["serve"])
    client = AbletonClient(port=server.port)
    try:
        with pytest.raises(CommandError) as exc:
            client.send("partial")
        assert exc.value.error_type == "PartialApplyError"
        assert exc.value.applied == ["name", "volume"]
    finally:
        client.close()
        server.close()


def test_reconnects_once_after_dead_socket():
    # First connection dies immediately (Live restarted); second serves.
    # Uses a read-only command: only those are eligible for auto-resend.
    server = ScriptedServer(["drop", "serve"])
    client = AbletonClient(port=server.port)
    try:
        assert client.send("get_tracks") == {"echo": "get_tracks"}
    finally:
        client.close()
        server.close()


def test_cannot_connect_is_helpful():
    client = AbletonClient(port=1)  # nothing listens there
    with pytest.raises(AbletonConnectionError, match="Ableton Live running"):
        client.send("anything")


def test_timeout_does_not_resend():
    server = ScriptedServer(["stall", "serve"])
    client = AbletonClient(port=server.port, timeout=0.3)
    try:
        with pytest.raises(AbletonConnectionError, match="No response within"):
            client.send("slow_thing")
        # The stalled server got the request exactly once — no dangerous resend.
        assert len(server.requests_received) == 1
    finally:
        client.close()
        server.close()


def test_read_only_command_resends_after_delivered_but_lost_response():
    # get_session_overview is read_only in the registry → safe to resend.
    server = ScriptedServer(["read_then_die", "serve"])
    client = AbletonClient(port=server.port)
    try:
        result = client.send("get_session_overview")
        assert result == {"echo": "get_session_overview"}
        assert len(server.requests_received) == 2  # original + safe resend
    finally:
        client.close()
        server.close()


def test_write_command_never_resends_after_delivered_but_lost_response():
    # delete_track is destructive → the client must refuse to auto-resend.
    server = ScriptedServer(["read_then_die", "serve"])
    client = AbletonClient(port=server.port)
    try:
        with pytest.raises(AbletonConnectionError, match="may or may not have executed"):
            client.send("delete_track", track_index=0)
        assert len(server.requests_received) == 1  # delivered exactly once
    finally:
        client.close()
        server.close()


def test_wire_specials_resend():
    server = ScriptedServer(["read_then_die", "serve"])
    client = AbletonClient(port=server.port)
    try:
        assert client.send("ping") == {"echo": "ping"}
        assert len(server.requests_received) == 2
    finally:
        client.close()
        server.close()
