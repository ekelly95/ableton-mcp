"""Importable test helpers: wire codec, command runner, reusable fakes.

Deliberately not in conftest.py — conftest is pytest's fixture-discovery
file, and importing plain helpers from it is an anti-pattern. Fixtures stay
there; anything tests import by name lives here.
"""

import json
import socket
import struct
from typing import Any

from control_surface.commands import REGISTRY
from control_surface.config import VERSION
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
