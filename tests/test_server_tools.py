"""MCP server: tool generation from the registry and end-to-end dispatch
through a real in-memory MCP session (the exact wiring 1.0 got wrong)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from control_surface.commands import REGISTRY
from mcp_server.client import AbletonConnectionError, CommandError
from mcp_server.server import build_server, registry_tools

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeAbletonClient:
    """Stands in for the socket client; canned responses per command."""

    def __init__(self, connected: bool = True):
        self.connected = connected
        self.sent = []

    def ping(self):
        if not self.connected:
            return None
        from control_surface.config import VERSION

        return {
            "pong": True,
            "version": VERSION,
            "schema_hash": REGISTRY.schema_hash(),
            "command_count": len(REGISTRY),
        }

    def send(self, command, **params):
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


class TestToolGeneration:
    def test_every_registry_command_is_a_tool(self):
        tool_names = {t.name for t in registry_tools()}
        for command in REGISTRY.list_commands():
            assert command in tool_names

    def test_bridge_status_included(self):
        tool_names = [t.name for t in registry_tools()]
        assert "get_bridge_status" in tool_names
        # +2 = get_bridge_status + get_audio_levels
        assert len(tool_names) == len(REGISTRY) + 2

    def test_wire_specials_are_not_tools(self):
        tool_names = {t.name for t in registry_tools()}
        assert "ping" not in tool_names
        assert "list_commands" not in tool_names

    def test_schemas_are_real(self):
        tools = {t.name: t for t in registry_tools()}
        create_clip = tools["create_clip"]
        props = create_clip.inputSchema["properties"]
        assert set(create_clip.inputSchema["required"]) == {
            "track_index",
            "slot_index",
            "length_beats",
        }
        assert props["length_beats"]["minimum"] == 0.25
        assert create_clip.inputSchema["additionalProperties"] is False

    def test_annotations_mapped(self):
        tools = {t.name: t for t in registry_tools()}
        assert tools["get_notes"].annotations.readOnlyHint is True
        assert tools["get_notes"].annotations.destructiveHint is False
        assert tools["delete_track"].annotations.destructiveHint is True

    def test_output_schema_on_reads(self):
        tools = {t.name: t for t in registry_tools()}
        assert tools["get_transport_state"].outputSchema is not None
        assert "tempo" in tools["get_transport_state"].outputSchema["properties"]


@pytest.mark.anyio
class TestEndToEnd:
    async def _session(self, client):
        from mcp.shared.memory import create_connected_server_and_client_session

        return create_connected_server_and_client_session(build_server(client))

    async def test_list_tools_over_protocol(self):
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.list_tools()
            names = [t.name for t in result.tools]
            assert "get_session_overview" in names
            assert "add_notes" in names

    async def test_call_tool_structured_and_text(self):
        fake = FakeAbletonClient()
        async with await self._session(fake) as session:
            result = await session.call_tool("get_transport_state", {})
            assert result.isError is False
            assert result.structuredContent["tempo"] == 120.0
            text = json.loads(result.content[0].text)
            assert text["tempo"] == 120.0
            assert ("get_transport_state", {}) in fake.sent

    async def test_command_error_becomes_tool_error(self):
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("delete_track", {"track_index": 99})
            assert result.isError is True
            assert "LiveAPIError" in result.content[0].text

    async def test_connection_error_becomes_tool_error_with_hint(self):
        async with await self._session(FakeAbletonClient(connected=False)) as session:
            result = await session.call_tool("get_transport_state", {})
            assert result.isError is True
            assert "not reachable" in result.content[0].text

    async def test_bridge_status_disconnected(self):
        async with await self._session(FakeAbletonClient(connected=False)) as session:
            result = await session.call_tool("get_bridge_status", {})
            assert result.isError is False
            assert result.structuredContent["connected"] is False
            assert "Preferences" in result.structuredContent["hint"]

    async def test_bridge_status_connected_in_sync(self):
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("get_bridge_status", {})
            assert result.structuredContent["connected"] is True
            assert result.structuredContent["schema_in_sync"] is True

    async def test_pitch_name_survives_real_input_validation(self):
        """Audit finding: the ADVERTISED schema must accept 'A3' in update_notes
        modifications — direct-handler tests bypass the SDK's input validation,
        which is exactly where this used to break."""
        fake = FakeAbletonClient()
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "update_notes",
                {
                    "track_index": 0,
                    "slot_index": 0,
                    "modifications": [{"note_id": 1, "pitch": "A3", "velocity": 90}],
                },
            )
            assert result.isError is False, result.content[0].text
            sent_command, sent_params = fake.sent[-1]
            assert sent_command == "update_notes"
            assert sent_params["modifications"][0]["pitch"] == "A3"

    async def test_delete_arrangement_clip_requires_guard_in_schema(self):
        async with await self._session(FakeAbletonClient()) as session:
            tools = {t.name: t for t in (await session.list_tools()).tools}
            required = tools["delete_arrangement_clip"].inputSchema["required"]
            assert "expected_start_time" in required


def test_importing_server_writes_nothing_to_stdout():
    code = "import mcp_server.server; import mcp_server"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout == "", f"stdout must stay clean for MCP stdio, got: {result.stdout!r}"
