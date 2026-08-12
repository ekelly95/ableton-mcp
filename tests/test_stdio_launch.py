"""Launch the server exactly like a real client does — as a subprocess over
stdio — and complete an MCP handshake plus the startup calls clients actually
make. No Live required."""

import sys

import pytest

from control_surface.commands import REGISTRY


@pytest.mark.anyio
async def test_stdio_handshake_and_tools():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert "get_session_overview" in names
            assert "add_notes" in names
            # +2 = get_bridge_status + get_audio_levels
            assert len(names) == len(REGISTRY) + 3  # bridge_status, audio_levels, transform_clip


@pytest.mark.anyio
async def test_stdio_startup_probes_do_not_error():
    """Codex's real startup sequence: handshake, then ask for resources and
    prompts. A -32601 here is what makes it declare the server not initialized."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.capabilities.resources is not None
            assert init.capabilities.prompts is not None
            assert (await session.list_resources()).resources == []
            assert (await session.list_resource_templates()).resourceTemplates == []
            assert (await session.list_prompts()).prompts == []
