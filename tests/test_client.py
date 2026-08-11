"""AbletonClient: framing, reconnect-once, and the no-resend-on-timeout rule."""

import json
import socket
import struct
import threading

import pytest

from mcp_server.client import AbletonClient, AbletonConnectionError, CommandError


class ScriptedServer:
    """Tiny localhost server whose per-connection behaviour is scripted.

    behaviours: list of strings, one per accepted connection:
      "serve"      — answer requests normally until the client disconnects
      "drop"       — accept, then immediately close (simulates dead socket)
      "stall"      — accept, read the request, never answer
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
                    request = self._read(conn)
                    if request is None:
                        break
                    self.requests_received.append(request)
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
                    body = json.dumps(response).encode()
                    conn.sendall(struct.pack(">I", len(body)) + body)
            except OSError:
                pass
            finally:
                conn.close()
        self._sock.close()

    def _read(self, conn):
        header = b""
        while len(header) < 4:
            chunk = conn.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk
        length = struct.unpack(">I", header)[0]
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        return json.loads(payload.decode())

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


def test_reconnects_once_after_dead_socket():
    # First connection dies immediately (Live restarted); second serves.
    server = ScriptedServer(["drop", "serve"])
    client = AbletonClient(port=server.port)
    try:
        assert client.send("recovered") == {"echo": "recovered"}
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
