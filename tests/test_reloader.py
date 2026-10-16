"""Hot reload: command handlers reload from disk without restarting Live."""

import socket
import sys

import pytest

from control_surface import reloader
from control_surface.commands import REGISTRY
from control_surface.socket_server import SocketServer
from tests.helpers import read_frame, run_command, write_frame
from tests.mock_live import MockControlSurface


def test_reload_swaps_in_fresh_handlers(registry, ctx):
    old_handler = REGISTRY.get("get_notes").handler
    old_module = sys.modules["control_surface.commands.clips"]
    names, schema = REGISTRY.list_commands(), REGISTRY.schema_hash()

    result = run_command(registry, ctx, "reload_commands")

    assert result["command_count"] == len(names)
    assert REGISTRY.list_commands() == names
    assert REGISTRY.schema_hash() == schema == result["schema_hash"]
    assert REGISTRY.get("get_notes").handler is not old_handler
    assert sys.modules["control_surface.commands.clips"] is not old_module


def test_failed_reload_restores_the_previous_commands(registry, ctx, monkeypatch):
    names = REGISTRY.list_commands()
    handler = REGISTRY.get("get_notes").handler
    module = sys.modules["control_surface.commands.clips"]

    def broken(name):
        raise SyntaxError("half-saved file")

    monkeypatch.setattr(reloader.importlib, "import_module", broken)
    with pytest.raises(SyntaxError):
        reloader.reload_command_modules()

    assert REGISTRY.list_commands() == names
    assert REGISTRY.get("get_notes").handler is handler
    assert sys.modules["control_surface.commands.clips"] is module


def test_reload_over_the_wire_then_keep_working(song):
    srv = SocketServer(
        MockControlSurface(song), host="127.0.0.1", port=0, use_tcp=True, registry=REGISTRY
    )
    srv.start()
    sock = socket.create_connection(("127.0.0.1", srv.bound_port), timeout=5)
    try:
        write_frame(sock, {"type": "reload_commands", "params": {}, "id": "r1"})
        assert read_frame(sock)["status"] == "success"
        write_frame(sock, {"type": "get_transport_state", "params": {}, "id": "r2"})
        assert read_frame(sock)["result"]["tempo"] == 120.0
    finally:
        sock.close()
        srv.stop()


def test_reload_is_not_an_mcp_tool():
    assert REGISTRY.get("reload_commands").internal is True
    assert "reload_commands" not in {t["name"] for t in REGISTRY.generate_mcp_tools()}
