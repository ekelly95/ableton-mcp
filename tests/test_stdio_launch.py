"""Launch the server exactly like Claude Desktop does — as a subprocess over
stdio — and complete an MCP handshake plus list_tools. No Live required."""

import sys

import pytest

from control_surface.commands import REGISTRY


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
            assert len(names) == len(REGISTRY) + 2
