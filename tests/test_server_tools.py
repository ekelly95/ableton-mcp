"""MCP server: tool generation from the registry and end-to-end dispatch
through a real in-memory MCP session (the exact wiring 1.0 got wrong)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from control_surface.commands import REGISTRY
from mcp_server.server import build_server, registry_tools
from tests.helpers import FakeAbletonClient

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestToolGeneration:
    def test_every_registry_command_is_a_tool(self):
        tool_names = {t.name for t in registry_tools()}
        for command in REGISTRY.list_commands():
            assert command in tool_names

    def test_bridge_status_included(self):
        tool_names = [t.name for t in registry_tools()]
        assert "get_bridge_status" in tool_names
        # bridge_status, audio_levels, transform_clip, search_library, find_similar
        assert len(tool_names) == len(REGISTRY) + 5

    def test_library_tools_are_read_only(self):
        tools = {t.name: t for t in registry_tools()}
        for name in ("search_library", "find_similar"):
            assert tools[name].annotations.readOnlyHint is True
            assert tools[name].annotations.destructiveHint is False

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

    async def test_tool_count_matches_readme(self):
        # Guards the README's "46 tools" headline — a feature round that adds
        # tools must update both this number and the README sentence.
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.list_tools()
            names = [t.name for t in result.tools]
            assert len(names) == len(set(names)) == 46

    async def test_optional_capability_probes_answer_instead_of_erroring(self):
        """Codex asks every server for resources, resource templates and prompts
        at startup and reads a -32601 refusal as the server failing to start, so
        these three must answer even though the server has none of them."""
        async with await self._session(FakeAbletonClient()) as session:
            assert (await session.list_resources()).resources == []
            assert (await session.list_resource_templates()).resourceTemplates == []
            assert (await session.list_prompts()).prompts == []

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

    async def test_compact_text_emission(self):
        # The text copy the model reads must be compact JSON, never indent=2.
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("get_transport_state", {})
            assert "\n" not in result.content[0].text
            assert '": ' not in result.content[0].text


class NotesFakeClient(FakeAbletonClient):
    """Fake with a real notes store so notation/transforms round-trips work."""

    def __init__(self):
        super().__init__()
        self.notes = [
            {
                "note_id": 1,
                "pitch": 60,
                "pitch_name": "C3",
                "start_time": 0.0,
                "duration": 1.0,
                "velocity": 100.0,
            },
            {
                "note_id": 2,
                "pitch": 62,
                "pitch_name": "D3",
                "start_time": 0.5,
                "duration": 1.0,
                "velocity": 100.0,
            },
        ]

    def send(self, command, **params):
        if command == "get_notes":
            self.sent.append((command, params))
            return {
                "notes": [dict(n) for n in self.notes],
                "note_count": len(self.notes),
                "truncated": False,
                "clip_length": 4.0,
            }
        if command in ("update_notes", "remove_notes", "add_notes"):
            self.sent.append((command, params))
            return {"ok": True}
        return super().send(command, **params)


@pytest.mark.anyio
class TestNotationAndTransforms:
    async def _session(self, client):
        from mcp.shared.memory import create_connected_server_and_client_session

        return create_connected_server_and_client_session(build_server(client))

    async def test_add_notes_notation_expanded_server_side(self):
        fake = FakeAbletonClient()
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "add_notes",
                {"track_index": 0, "slot_index": 0, "notation": "v90 n/8 C3 E3 1|1"},
            )
            assert result.isError is False
        command, params = next(c for c in fake.sent if c[0] == "add_notes")
        assert "notation" not in params  # Live never sees the dialect
        assert [n["pitch"] for n in params["notes"]] == [60, 64]
        assert all(n["velocity"] == 90.0 for n in params["notes"])

    async def test_add_notes_with_transforms_applied(self):
        fake = FakeAbletonClient()
        async with await self._session(fake) as session:
            await session.call_tool(
                "add_notes",
                {
                    "track_index": 0,
                    "slot_index": 0,
                    "notation": "v100 C3 1|1 D3 1|2",
                    "transforms": "velocity = 60",
                },
            )
        _, params = next(c for c in fake.sent if c[0] == "add_notes")
        assert all(n["velocity"] == 60.0 for n in params["notes"])

    async def test_get_notes_compact_renders_notation(self):
        fake = NotesFakeClient()
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "get_notes", {"track_index": 0, "slot_index": 0, "format": "compact"}
            )
            assert result.isError is False
            payload = result.structuredContent
            assert "notes" not in payload
            assert "C3" in payload["notation"] and "D3" in payload["notation"]
        _, params = next(c for c in fake.sent if c[0] == "get_notes")
        assert "format" not in params  # stripped before Live

    async def test_transform_clip_diffs_by_note_id(self):
        fake = NotesFakeClient()
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "transform_clip",
                {"track_index": 0, "slot_index": 0, "transforms": "D3: v0; C3: velocity = 40"},
            )
            assert result.isError is False
            assert result.structuredContent["removed"] == 1
            assert result.structuredContent["updated"] == 1
            assert result.structuredContent["added"] == 0
        removed = next(c for c in fake.sent if c[0] == "remove_notes")
        assert removed[1]["note_ids"] == [2]
        updated = next(c for c in fake.sent if c[0] == "update_notes")
        assert updated[1]["modifications"] == [{"note_id": 1, "velocity": 40.0}]

    async def test_transform_clip_note_ops_add_notes(self):
        fake = NotesFakeClient()
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "transform_clip",
                {"track_index": 0, "slot_index": 0, "transforms": "C3: ratchet(2)"},
            )
            assert result.isError is False
            # ratchet replaces the original (1 removed) with 2 new pieces
            assert result.structuredContent["removed"] == 1
            assert result.structuredContent["added"] == 2

    async def test_transform_clip_refuses_truncated_read(self):
        # A clip past the 2000-note read cap must be refused outright —
        # transforming only the readable prefix would silently leave the rest
        # of the clip untouched while reporting the capped count as the total.
        fake = NotesFakeClient()
        original_send = fake.send

        def send(command, **params):
            result = original_send(command, **params)
            if command == "get_notes":
                result["truncated"] = True
            return result

        fake.send = send
        async with await self._session(fake) as session:
            result = await session.call_tool(
                "transform_clip",
                {"track_index": 0, "slot_index": 0, "transforms": "velocity = 80"},
            )
            assert result.isError is True
            assert "read limit" in result.content[0].text
        writes = [c for c in fake.sent if c[0] in ("remove_notes", "update_notes", "add_notes")]
        assert writes == []  # the refusal fired before any write

    async def test_bridge_status_connected_in_sync(self):
        async with await self._session(FakeAbletonClient()) as session:
            result = await session.call_tool("get_bridge_status", {})
            assert result.structuredContent["connected"] is True
            assert result.structuredContent["schema_in_sync"] is True

    async def test_pitch_name_survives_real_input_validation(self):
        """The ADVERTISED schema must accept 'A3' in update_notes
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
