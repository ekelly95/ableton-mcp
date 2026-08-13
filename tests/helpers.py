"""Importable test helpers: wire codec, command runner, reusable fakes.

Deliberately not in conftest.py — conftest is pytest's fixture-discovery
file, and importing plain helpers from it is an anti-pattern. Fixtures stay
there; anything tests import by name lives here.
"""

import json
import socket
import struct
import threading
from typing import Any

from control_surface.commands import REGISTRY
from control_surface.config import VERSION
from control_surface.registry import CommandRegistry, ParamSchema, ParamType
from mcp_server.client import AbletonConnectionError, CommandError

# --- wire codec (4-byte big-endian length + UTF-8 JSON) ---


def read_exact(sock: socket.socket, size: int) -> bytes | None:
    """Exactly `size` bytes; None on clean EOF at a frame boundary.

    A close mid-frame is a protocol violation and fails the test outright.
    """
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            assert not data, "connection closed mid-frame"
            return None
        data.extend(chunk)
    return bytes(data)


def read_frame(sock: socket.socket) -> dict | None:
    """Next length-prefixed JSON frame; None if the peer closed cleanly."""
    header = read_exact(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    if length == 0:
        return {}
    body = read_exact(sock, length)
    assert body is not None, "connection closed mid-frame"
    return json.loads(body.decode("utf-8"))


def write_frame(sock: socket.socket, message: dict) -> None:
    body = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


# --- command runner ---


def run_command(registry, ctx, name, /, **params):
    """Validate params exactly like the socket server would, then execute.

    Runs the handler through schedule_message so each command is exactly one
    scheduled task — matching the real one-marshal-per-request design. This is
    what makes the mock's deferred current_song_time (applied at task
    boundaries) behave in tests as it does against real Live.
    """
    schema = registry.get(name)
    assert schema is not None, f"command {name} not registered"
    validated = schema.validate_params(params)
    box = {}

    def task():
        box["result"] = schema.handler(ctx, **validated)

    ctx.control_surface.schedule_message(1, task)
    return box["result"]


# --- scripted transport server ---


class ScriptedServer:
    """Tiny server whose per-connection behaviour is scripted; TCP or AF_UNIX.

    behaviours: list of strings, one per accepted connection:
      "serve"         — answer requests normally until the client disconnects
      "drop"          — accept, then immediately close (simulates dead socket)
      "stall"         — accept, read the request, never answer
      "read_then_die" — read (and record) ONE request, then close without
                        replying: the request WAS delivered, response lost
      "garbage"       — read ONE request, reply with a correctly framed
                        non-JSON payload, then close

    One implementation for both transports, so the client's reconnect/resend
    matrix can be scripted identically over TCP and Unix sockets. Construct
    via ScriptedServer.tcp(...) (sets .port) or ScriptedServer.unix(path, ...).
    """

    def __init__(self, sock: socket.socket, behaviours):
        self.behaviours = list(behaviours)
        self.requests_received = []
        self._sock = sock
        self._sock.listen(5)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @classmethod
    def tcp(cls, behaviours) -> "ScriptedServer":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        server = cls(sock, behaviours)
        server.port = sock.getsockname()[1]
        return server

    @classmethod
    def unix(cls, path: str, behaviours) -> "ScriptedServer":
        # Callers are responsible for the AF_UNIX skipif guard.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        return cls(sock, behaviours)

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
                    if behaviour == "garbage":
                        body = b"this is not json"
                        conn.sendall(struct.pack(">I", len(body)) + body)
                        break
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


def echo_registry() -> CommandRegistry:
    """Minimal one-command registry for socket-server tests."""
    registry = CommandRegistry()

    @registry.register(
        "echo",
        params=[ParamSchema("value", ParamType.STRING)],
        description="Echo a value",
    )
    def echo(ctx, value):
        return {"echoed": value}

    return registry


# --- reusable fakes ---


class ImmediateControlSurface:
    """Executes scheduled tasks synchronously, like tests want and unlike Live."""

    def schedule_message(self, delay, callback):
        callback()

    def song(self):
        return None

    def application(self):
        return None


class FakeAbletonClient:
    """Stands in for the socket client; canned responses per command."""

    def __init__(self, connected: bool = True):
        self.connected = connected
        self.sent = []

    def ping(self):
        if not self.connected:
            return None
        return {
            "pong": True,
            "version": VERSION,
            "schema_hash": REGISTRY.schema_hash(),
            "command_count": len(REGISTRY),
        }

    def send(self, command: str, **params: Any):
        if not self.connected:
            raise AbletonConnectionError("Cannot connect (fake)")
        self.sent.append((command, params))
        if command == "get_transport_state":
            return {
                "is_playing": False,
                "tempo": 120.0,
                "signature_numerator": 4,
                "signature_denominator": 4,
                "metronome": False,
                "loop": {"enabled": False, "start": 0.0, "length": 4.0},
                "current_song_time": 0.0,
            }
        if command == "delete_track":
            raise CommandError("Track index 99 out of range", error_type="LiveAPIError")
        return {"ok": True, "command": command}

    # The server dispatches through the seek-resolving wrapper; canned
    # responses never enter the "seeking" phase, so a single send suffices.
    def send_resolving_seek(self, command: str, **params: Any):
        return self.send(command, **params)
