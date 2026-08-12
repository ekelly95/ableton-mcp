"""AF_UNIX transport: client, control-surface server, and the full pair.

These tests are skipped where AF_UNIX is unavailable and run on the macOS CI
job. They cover the transport in isolation; real-Live macOS verification is
recorded in the architecture document and development record.
"""

import socket
import tempfile
import uuid
from pathlib import Path

import pytest

from control_surface.socket_server import SocketServer
from mcp_server.client import AbletonClient, AbletonConnectionError
from tests.helpers import (
    ImmediateControlSurface,
    ScriptedServer,
    echo_registry,
    read_frame,
    write_frame,
)

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable on this platform"
)


@pytest.fixture()
def socket_path():
    # tempfile.mkdtemp, not tmp_path: macOS caps AF_UNIX paths at ~104 bytes
    # and pytest's tmp_path (per-test naming) can blow past it.
    workdir = tempfile.mkdtemp(prefix="amcp")
    yield str(Path(workdir) / "bridge.sock")


def _unix_client(path: str, **kwargs) -> AbletonClient:
    return AbletonClient(socket_path=path, use_tcp=False, **kwargs)


def test_client_round_trip_over_unix_socket(socket_path):
    server = ScriptedServer.unix(socket_path, ["serve"])
    client = _unix_client(socket_path)
    try:
        assert client.send("hello") == {"echo": "hello"}
        assert client.send("again") == {"echo": "again"}
    finally:
        client.close()
        server.close()


def test_client_reconnects_once_over_unix_socket(socket_path):
    server = ScriptedServer.unix(socket_path, ["drop", "serve"])
    client = _unix_client(socket_path)
    try:
        # Read-only command: eligible for the one reconnect-and-resend.
        assert client.send("get_tracks") == {"echo": "get_tracks"}
    finally:
        client.close()
        server.close()


def test_timeout_does_not_resend_over_unix(socket_path):
    # Same no-resend rule test_client runs over TCP — recv timeout semantics
    # are the one part of that rule the transport could plausibly change.
    server = ScriptedServer.unix(socket_path, ["stall", "serve"])
    client = _unix_client(socket_path, timeout=0.3)
    try:
        with pytest.raises(AbletonConnectionError, match="No response within"):
            client.send("slow_thing")
        assert len(server.requests_received) == 1
    finally:
        client.close()
        server.close()


def test_missing_socket_error_names_the_path(socket_path):
    client = _unix_client(socket_path)  # nothing listening
    with pytest.raises(AbletonConnectionError, match="bridge.sock"):
        client.send("anything")


@pytest.fixture()
def unix_server(socket_path):
    srv = SocketServer(
        ImmediateControlSurface(),
        socket_path=socket_path,
        use_tcp=False,
        registry=echo_registry(),
    )
    srv.start()
    yield srv, socket_path
    srv.stop()


def test_socket_server_serves_and_cleans_up(unix_server):
    srv, path = unix_server
    assert Path(path).exists()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(path)
    try:
        write_frame(sock, {"type": "ping", "id": "u1"})
        response = read_frame(sock)
        assert response["status"] == "success"
        assert response["result"]["pong"] is True
    finally:
        sock.close()
    srv.stop()
    assert not Path(path).exists()  # socket file unlinked on stop


def test_socket_server_replaces_stale_socket_file(socket_path):
    # A dead server leaves its socket file behind; a new bind must replace it.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(socket_path)
    stale.close()  # closes the socket, leaves the filesystem entry

    srv = SocketServer(
        ImmediateControlSurface(),
        socket_path=socket_path,
        use_tcp=False,
        registry=echo_registry(),
    )
    srv.start()
    try:
        client = _unix_client(socket_path)
        try:
            result = client.send("echo", value="alive")
            assert result == {"echoed": "alive"}
        finally:
            client.close()
    finally:
        srv.stop()


def test_full_pair_over_unix_socket(unix_server):
    """AbletonClient <-> SocketServer over AF_UNIX — everything except Live.

    This is the round trip the README's macOS CI-green claim rests on.
    """
    _, path = unix_server
    client = _unix_client(path)
    try:
        assert client.send("echo", value="mac")["echoed"] == "mac"
        ping = client.send("ping")
        assert ping["pong"] is True
        assert len(ping["schema_hash"]) == 64
    finally:
        client.close()


def test_full_pair_request_id_dedupe_over_unix(unix_server):
    _, path = unix_server
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(path)
    try:
        message = {"type": "echo", "params": {"value": "x"}, "id": str(uuid.uuid4())}
        write_frame(sock, message)
        first = read_frame(sock)
        write_frame(sock, message)
        second = read_frame(sock)
        assert first == second  # cached replay, same wire behaviour as TCP
    finally:
        sock.close()
