"""End-to-end socket framing and command dispatch over a real localhost socket."""

import json
import socket
import struct
import uuid

import pytest

from control_surface.registry import CommandRegistry, LiveAPIError, ParamSchema, ParamType
from control_surface.socket_server import SocketServer


class ImmediateControlSurface:
    def schedule_message(self, delay, callback):
        callback()

    def song(self):
        return None

    def application(self):
        return None


EXECUTION_COUNTS = {"counter": 0}


def build_test_registry() -> CommandRegistry:
    registry = CommandRegistry()

    @registry.register(
        "echo",
        params=[ParamSchema("value", ParamType.STRING)],
        description="Echo a value",
    )
    def echo(ctx, value):
        return {"echoed": value}

    @registry.register("count_executions")
    def count_executions(ctx):
        EXECUTION_COUNTS["counter"] += 1
        return {"count": EXECUTION_COUNTS["counter"]}

    @registry.register(
        "guarded",
        params=[ParamSchema("n", ParamType.INT, min_value=0, max_value=10)],
    )
    def guarded(ctx, n):
        return {"n": n}

    @registry.register("live_fail")
    def live_fail(ctx):
        raise LiveAPIError("Track 99 does not exist")

    return registry


@pytest.fixture()
def server():
    srv = SocketServer(
        ImmediateControlSurface(),
        host="127.0.0.1",
        port=0,
        use_tcp=True,
        registry=build_test_registry(),
    )
    srv.start()
    yield srv
    srv.stop()


def send_request(port: int, message: dict, sock: socket.socket = None) -> dict:
    own_socket = sock is None
    if own_socket:
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        body = json.dumps(message).encode("utf-8")
        sock.sendall(struct.pack(">I", len(body)) + body)
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            assert chunk, "connection closed while reading header"
            header += chunk
        length = struct.unpack(">I", header)[0]
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            assert chunk, "connection closed while reading body"
            payload += chunk
        return json.loads(payload.decode("utf-8"))
    finally:
        if own_socket:
            sock.close()


def test_ping(server):
    response = send_request(server.bound_port, {"type": "ping", "id": "abc"})
    assert response["status"] == "success"
    assert response["id"] == "abc"
    assert response["result"]["pong"] is True
    assert response["result"]["version"]
    assert len(response["result"]["schema_hash"]) == 64


def test_echo_round_trip(server):
    response = send_request(
        server.bound_port,
        {"type": "echo", "params": {"value": "hello"}, "id": str(uuid.uuid4())},
    )
    assert response["status"] == "success"
    assert response["result"] == {"echoed": "hello"}


def test_multiple_requests_same_connection(server):
    sock = socket.create_connection(("127.0.0.1", server.bound_port), timeout=5)
    try:
        for i in range(3):
            response = send_request(
                server.bound_port,
                {"type": "guarded", "params": {"n": i}, "id": str(i)},
                sock=sock,
            )
            assert response["status"] == "success"
            assert response["result"]["n"] == i
    finally:
        sock.close()


def test_validation_error_shape(server):
    response = send_request(
        server.bound_port, {"type": "guarded", "params": {"n": 99}, "id": "v"}
    )
    assert response["status"] == "error"
    assert response["error_type"] == "ValidationError"
    assert response["param"] == "n"


def test_unknown_command(server):
    response = send_request(server.bound_port, {"type": "nope", "id": "u"})
    assert response["status"] == "error"
    assert response["error_type"] == "ValidationError"
    assert "Unknown command" in response["error"]


def test_live_api_error_taxonomy(server):
    response = send_request(server.bound_port, {"type": "live_fail", "id": "l"})
    assert response["status"] == "error"
    assert response["error_type"] == "LiveAPIError"
    assert "Track 99" in response["error"]


def test_list_commands_and_tools(server):
    listing = send_request(server.bound_port, {"type": "list_commands", "id": "1"})
    assert "echo" in listing["result"]["commands"]

    tools = send_request(server.bound_port, {"type": "get_mcp_tools", "id": "2"})
    names = [t["name"] for t in tools["result"]["tools"]]
    assert "echo" in names
    assert tools["result"]["schema_hash"]


def test_duplicate_request_id_executes_once(server):
    """A resent request (same id) must replay the cached response, not re-run."""
    EXECUTION_COUNTS["counter"] = 0
    message = {"type": "count_executions", "params": {}, "id": "dedupe-test-1"}
    first = send_request(server.bound_port, message)
    second = send_request(server.bound_port, message)
    assert first["status"] == "success"
    assert first["result"]["count"] == 1
    assert second == first  # cached replay, handler ran exactly once
    assert EXECUTION_COUNTS["counter"] == 1

    # A NEW id executes normally afterwards.
    third = send_request(
        server.bound_port, {"type": "count_executions", "params": {}, "id": "dedupe-test-2"}
    )
    assert third["result"]["count"] == 2


def test_oversized_message_rejected_then_server_survives(server):
    sock = socket.create_connection(("127.0.0.1", server.bound_port), timeout=5)
    try:
        sock.sendall(struct.pack(">I", 32 * 1024 * 1024))
        header = sock.recv(4)
        if header:
            length = struct.unpack(">I", header)[0]
            payload = b""
            while len(payload) < length:
                chunk = sock.recv(length - len(payload))
                if not chunk:
                    break
                payload += chunk
            response = json.loads(payload.decode("utf-8"))
            assert response["status"] == "error"
    finally:
        sock.close()

    # A fresh connection must still work after the bad client
    response = send_request(server.bound_port, {"type": "ping", "id": "again"})
    assert response["status"] == "success"
