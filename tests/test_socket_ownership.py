"""Per-user Unix socket: location, directory permissions, and the client's
refusal to talk to a socket another account owns."""

import os
import socket
import stat
import tempfile
from pathlib import Path

import pytest

from control_surface import config
from control_surface.socket_server import SocketServer
from mcp_server import client as client_module
from mcp_server.client import AbletonClient, AbletonConnectionError
from tests.helpers import ImmediateControlSurface, ScriptedServer, echo_registry

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX") or not hasattr(os, "getuid"),
    reason="Unix sockets / uids unavailable on this platform",
)


@pytest.fixture()
def workdir():
    # mkdtemp, not tmp_path: macOS caps AF_UNIX paths at ~104 bytes.
    return Path(tempfile.mkdtemp(prefix="amcp"))


def test_default_socket_is_not_in_a_shared_directory():
    assert config.SOCKET_PATH != config.LEGACY_SOCKET_PATH
    if config.SOCKET_PATH.startswith("/tmp/"):
        # Long-home fallback: still per-uid.
        assert str(os.getuid()) in config.SOCKET_PATH
    else:
        assert Path(config.SOCKET_PATH).parent == config.RUNTIME_DIR
        assert str(Path.home()) in config.SOCKET_PATH
    assert len(os.fsencode(config.SOCKET_PATH)) <= 104


def test_server_creates_owner_only_runtime_dir(workdir):
    path = workdir / "nested" / "bridge.sock"
    srv = SocketServer(
        ImmediateControlSurface(), socket_path=str(path), use_tcp=False, registry=echo_registry()
    )
    srv.start()
    try:
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        client = AbletonClient(socket_path=str(path), use_tcp=False)
        try:
            assert client.send("echo", value="hi") == {"echoed": "hi"}
        finally:
            client.close()
    finally:
        srv.stop()
    assert not path.exists()


def test_server_refuses_to_delete_a_regular_file_at_the_socket_path(workdir):
    path = workdir / "bridge.sock"
    path.write_text("not a socket")
    srv = SocketServer(
        ImmediateControlSurface(), socket_path=str(path), use_tcp=False, registry=echo_registry()
    )
    with pytest.raises(OSError, match="not a socket"):
        srv.start()
    assert path.read_text() == "not a socket"


def test_client_refuses_a_socket_owned_by_another_account(workdir, monkeypatch):
    path = str(workdir / "bridge.sock")
    server = ScriptedServer.unix(path, ["serve"])
    try:
        # Pretend to be a different user than the socket's owner.
        real_uid = os.getuid()
        monkeypatch.setattr(client_module.os, "getuid", lambda: real_uid + 1)
        client = AbletonClient(socket_path=path, use_tcp=False)
        with pytest.raises(AbletonConnectionError, match="owned by another account"):
            client.send("hello")
        assert server.requests_received == []  # nothing was sent
    finally:
        server.close()


def test_client_accepts_its_own_socket(workdir):
    path = str(workdir / "bridge.sock")
    server = ScriptedServer.unix(path, ["serve"])
    client = AbletonClient(socket_path=path, use_tcp=False)
    try:
        assert client.send("hello") == {"echo": "hello"}
    finally:
        client.close()
        server.close()


def test_missing_socket_points_at_an_outdated_control_surface(workdir, monkeypatch):
    legacy = workdir / "legacy.sock"
    legacy.touch()
    monkeypatch.setattr(client_module, "LEGACY_SOCKET_PATH", str(legacy))
    client = AbletonClient(socket_path=str(workdir / "bridge.sock"), use_tcp=False)
    with pytest.raises(AbletonConnectionError, match="install_control_surface"):
        client.send("anything")


def test_missing_socket_without_legacy_has_no_upgrade_hint(workdir, monkeypatch):
    monkeypatch.setattr(client_module, "LEGACY_SOCKET_PATH", str(workdir / "absent.sock"))
    client = AbletonClient(socket_path=str(workdir / "bridge.sock"), use_tcp=False)
    with pytest.raises(AbletonConnectionError) as exc:
        client.send("anything")
    assert "install_control_surface" not in str(exc.value)
