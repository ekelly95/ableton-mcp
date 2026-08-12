"""AF_UNIX transport: client, control-surface server, and the full pair.

This code path had NEVER run before 2.6.0 (Windows-only development) — treat
it as new code. These tests are skipped on Windows and run for real on the
macOS CI job, which is the only machine that can execute them: a green macos
run IS the macOS transport claim (no Mac hardware exists in-house).
"""

import socket
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

from control_surface.registry import CommandRegistry, ParamSchema, ParamType
from control_surface.socket_server import SocketServer
from mcp_server.client import AbletonClient, AbletonConnectionError
from tests.helpers import ImmediateControlSurface, read_frame, write_frame

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="Unix sockets unavailable on this platform"
)


@pytest.fixture()
def socket_path():
    # tempfile.mkdtemp, not tmp_path: macOS caps AF_UNIX paths at ~104 bytes
    # and pytest's tmp_path (per-test naming) can blow past it.
    workdir = tempfile.mkdtemp(prefix="amcp")
    yield str(Path(workdir) / "bridge.sock")


class UnixScriptedServer:
    """Minimal scripted server on a Unix socket (mirrors test_client's TCP one).

    behaviours, one per accepted connection: "serve" answers echo responses
    until disconnect; "drop" accepts then immediately closes.
    """

    def __init__(self, path: str, behaviours):
        self.behaviours = list(behaviours)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(5)
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
                    write_frame(
                        conn,
                        {
                            "status": "success",
                            "result": {"echo": request.get("type")},
                            "id": request.get("id"),
                        },
                    )
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


def _unix_client(path: str, **kwargs) -> AbletonClient:
    return AbletonClient(socket_path=path, use_tcp=False, **kwargs)


def test_client_round_trip_over_unix_socket(socket_path):
    server = UnixScriptedServer(socket_path, ["serve"])
    client = _unix_client(socket_path)
    try:
        assert client.send("hello") == {"echo": "hello"}
        assert client.send("again") == {"echo": "again"}
    finally:
        client.close()
        server.close()


def test_client_reconnects_once_over_unix_socket(socket_path):
    server = UnixScriptedServer(socket_path, ["drop", "serve"])
    client = _unix_client(socket_path)
    try:
        # Read-only command: eligible for the one reconnect-and-resend.
        assert client.send("get_tracks") == {"echo": "get_tracks"}
    finally:
        client.close()
        server.close()


def test_missing_socket_error_names_the_path(socket_path):
    client = _unix_client(socket_path)  # nothing listening
    with pytest.raises(AbletonConnectionError, match="bridge.sock"):
        client.send("anything")


def _echo_registry() -> CommandRegistry:
    registry = CommandRegistry()

    @registry.register(
        "echo",
        params=[ParamSchema("value", ParamType.STRING)],
        description="Echo a value",
    )
    def echo(ctx, value):
        return {"echoed": value}

    return registry


@pytest.fixture()
def unix_server(socket_path):
    srv = SocketServer(
        ImmediateControlSurface(),
        socket_path=socket_path,
        use_tcp=False,
        registry=_echo_registry(),
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
        registry=_echo_registry(),
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
