"""End-to-end socket framing and command dispatch over a real localhost socket."""

import socket
import struct
import time
import uuid

import pytest

import control_surface.thread_marshal as thread_marshal
from control_surface.errors import LiveAPIError, PartialApplyError
from control_surface.registry import CommandRegistry, ParamSchema, ParamType
from control_surface.socket_server import SocketServer
from tests.helpers import ImmediateControlSurface, read_frame, write_frame

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

    @registry.register("partial_fail")
    def partial_fail(ctx):
        raise PartialApplyError("sends", "Send index 7 out of range", applied=["name", "volume"])

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
        write_frame(sock, message)
        response = read_frame(sock)
        assert response is not None, "connection closed before a response arrived"
        return response
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
    response = send_request(server.bound_port, {"type": "guarded", "params": {"n": 99}, "id": "v"})
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


def test_partial_apply_error_carries_applied_on_wire(server):
    response = send_request(server.bound_port, {"type": "partial_fail", "id": "p"})
    assert response["status"] == "error"
    assert response["error_type"] == "PartialApplyError"
    assert response["applied"] == ["name", "volume"]
    assert "Already applied: name, volume" in response["error"]


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


def test_stop_unblocks_connected_client(server):
    """stop() must close the accepted client socket so the handler thread is
    not left parked in a blocking recv (the old 'did not stop cleanly' 5s
    stall on every Live script reload)."""
    sock = socket.create_connection(("127.0.0.1", server.bound_port), timeout=5)
    try:
        response = send_request(server.bound_port, {"type": "ping", "id": "s1"}, sock=sock)
        assert response["status"] == "success"

        start = time.time()
        server.stop()
        elapsed = time.time() - start
        assert elapsed < 3.0, f"stop() blocked for {elapsed:.1f}s on a connected client"
        assert server.is_running is False
    finally:
        sock.close()


class DropFirstControlSurface:
    """Drops the FIRST scheduled task (forcing a marshal timeout), runs later
    ones immediately — models Live briefly freezing, then recovering."""

    def __init__(self):
        self.dropped = False

    def schedule_message(self, delay, callback):
        if not self.dropped:
            self.dropped = True
            return
        callback()

    def song(self):
        return None

    def application(self):
        return None


def test_timed_out_request_not_cached_so_same_id_retries(monkeypatch):
    # Deadline refusal guarantees a timed-out request never executed, so a
    # resend with the SAME id must get a fresh attempt — not a replay of the
    # stale timeout error from the dedupe ring.
    monkeypatch.setattr(thread_marshal, "MARSHAL_GRACE_SECONDS", 0.05)
    registry = CommandRegistry()
    calls = {"n": 0}

    @registry.register("flaky", timeout=0.05)
    def flaky(ctx):
        calls["n"] += 1
        return {"n": calls["n"]}

    srv = SocketServer(
        DropFirstControlSurface(), host="127.0.0.1", port=0, use_tcp=True, registry=registry
    )
    srv.start()
    try:
        message = {"type": "flaky", "params": {}, "id": "retry-after-timeout"}
        first = send_request(srv.bound_port, message)
        assert first["status"] == "error"
        assert first["timeout"] is True

        second = send_request(srv.bound_port, message)
        assert second["status"] == "success"
        assert calls["n"] == 1  # ran exactly once — on the retry
    finally:
        srv.stop()


def test_oversized_message_rejected_then_server_survives(server):
    sock = socket.create_connection(("127.0.0.1", server.bound_port), timeout=5)
    try:
        sock.sendall(struct.pack(">I", 32 * 1024 * 1024))
        # The rejection must actually arrive as an error frame — the old
        # version of this assertion sat inside `if header:` and passed
        # vacuously when the server just closed the connection.
        response = read_frame(sock)
        assert response is not None, "server closed without sending the rejection"
        assert response["status"] == "error"
        assert "too large" in response["error"]
    finally:
        sock.close()

    # A fresh connection must still work after the bad client
    response = send_request(server.bound_port, {"type": "ping", "id": "again"})
    assert response["status"] == "success"
