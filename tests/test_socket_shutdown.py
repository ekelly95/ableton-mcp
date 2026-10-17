"""SocketServer lifecycle and concurrency: prompt stop() and lock-free wire
specials."""

import socket
import threading
import time

import pytest

from control_surface.registry import CommandRegistry
from control_surface.socket_server import WIRE_SPECIALS, SocketServer
from tests.helpers import ImmediateControlSurface, echo_registry, read_frame, write_frame


def _tcp_server(registry=None) -> SocketServer:
    srv = SocketServer(
        ImmediateControlSurface(),
        host="127.0.0.1",
        port=0,
        use_tcp=True,
        registry=registry or echo_registry(),
    )
    srv.start()
    return srv


def _request(port: int, message: dict) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        write_frame(sock, message)
        return read_frame(sock)


def test_idle_stop_does_not_wait_out_the_accept_timeout():
    """Closing a listening socket doesn't wake accept() on Linux/macOS, so
    every stop() used to take a full SOCKET_ACCEPT_TIMEOUT (1 s) — once per
    Live script reload."""
    srv = _tcp_server()
    start = time.monotonic()
    srv.stop()
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"idle stop() took {elapsed:.2f}s"
    assert srv.is_running is False


def test_server_restarts_on_the_same_port_after_stop():
    srv = _tcp_server()
    port = srv.bound_port
    srv.stop()
    again = SocketServer(
        ImmediateControlSurface(),
        host="127.0.0.1",
        port=port,
        use_tcp=True,
        registry=echo_registry(),
    )
    again.start()
    try:
        assert _request(port, {"type": "ping", "id": "r"})["status"] == "success"
    finally:
        again.stop()


def test_wire_specials_match_the_client():
    from mcp_server.client import WIRE_SPECIALS as CLIENT_SPECIALS

    assert set(WIRE_SPECIALS) == set(CLIENT_SPECIALS)


@pytest.mark.parametrize("special", sorted(WIRE_SPECIALS))
def test_wire_specials_answer_while_a_command_holds_the_lock(special):
    """A slow command (load_item can take 120 s) holds the execution lock for
    its whole main-thread round trip. Health checks and tool listings from
    other clients don't touch Live and must not queue behind it."""
    registry = CommandRegistry()
    entered = threading.Event()
    release = threading.Event()

    @registry.register("slow")
    def slow(ctx):
        entered.set()
        release.wait(10)
        return {"done": True}

    srv = _tcp_server(registry)
    slow_result = {}

    def run_slow():
        slow_result["r"] = _request(srv.bound_port, {"type": "slow", "id": "slow-1"})

    worker = threading.Thread(target=run_slow)
    worker.start()
    try:
        assert entered.wait(5), "slow command never started"
        start = time.monotonic()
        response = _request(srv.bound_port, {"type": special, "id": f"{special}-1"})
        elapsed = time.monotonic() - start
        assert response["status"] == "success"
        assert response["id"] == f"{special}-1"
        assert elapsed < 1.0, f"{special} waited {elapsed:.2f}s behind the lock"
    finally:
        release.set()
        worker.join(10)
        srv.stop()
    assert slow_result["r"]["result"] == {"done": True}


def test_ordinary_commands_still_serialize():
    registry = CommandRegistry()
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    @registry.register("work")
    def work(ctx):
        with guard:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with guard:
            active["now"] -= 1
        return {}

    srv = _tcp_server(registry)
    try:
        threads = [
            threading.Thread(
                target=_request, args=(srv.bound_port, {"type": "work", "id": f"w{i}"})
            )
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    finally:
        srv.stop()
    assert active["max"] == 1


def test_wire_specials_wait_out_a_hot_reload():
    """reload_commands clears the registry and re-imports on the main thread.
    A lock-free ping landing mid-reload must not report the half-built
    registry: its schema_hash would read as script drift."""
    registry = echo_registry()
    full_count = len(registry)
    srv = _tcp_server(registry)
    result = {}

    def ping():
        result["r"] = _request(srv.bound_port, {"type": "ping", "id": "p"})

    try:
        with registry.lock:
            saved = dict(registry._commands), {k: list(v) for k, v in registry._categories.items()}
            registry.clear()
            pinger = threading.Thread(target=ping)
            pinger.start()
            time.sleep(0.1)
            assert "r" not in result, "ping answered from a half-built registry"
            registry._commands.update(saved[0])
            registry._categories.update(saved[1])
        pinger.join(5)
    finally:
        srv.stop()
    assert result["r"]["result"]["command_count"] == full_count
