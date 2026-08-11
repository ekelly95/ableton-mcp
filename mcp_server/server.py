"""MCP server: exposes the control surface registry as MCP tools.

Tools are generated from control_surface's REGISTRY — imported directly, the
single source of truth. Nothing here defines a tool schema by hand except
get_bridge_status, which is server-local by nature (it reports whether Live
is reachable, so it cannot live inside Live).

All logging goes to stderr: stdout belongs to the MCP stdio transport.
"""

import json
import logging
import sys
from typing import Any, Optional

import anyio.to_thread
import mcp.types as types
from mcp.server.lowlevel import Server

# stderr-only logging BEFORE the control_surface import chain runs.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("ableton-mcp")

from control_surface.commands import REGISTRY  # noqa: E402 - after logging setup
from control_surface.config import VERSION  # noqa: E402

from .client import AbletonClient, AbletonConnectionError, CommandError  # noqa: E402

BRIDGE_STATUS_TOOL = types.Tool(
    name="get_bridge_status",
    description=(
        "Health check for the Ableton bridge: is Live reachable, do versions and "
        "schemas match. Call when other tools fail with connection errors."
    ),
    inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    outputSchema={
        "type": "object",
        "properties": {
            "connected": {"type": "boolean"},
            "package_version": {"type": "string"},
            "script_version": {"type": "string"},
            "schema_in_sync": {"type": "boolean"},
            "hint": {"type": "string"},
        },
    },
)


def registry_tools() -> list[types.Tool]:
    tools = []
    for spec in REGISTRY.generate_mcp_tools():
        tools.append(
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
                annotations=types.ToolAnnotations(**spec["annotations"]),
                outputSchema=spec.get("outputSchema"),
            )
        )
    tools.append(BRIDGE_STATUS_TOOL)
    return tools


class _DriftCheck:
    """Warn once if the copy deployed inside Live differs from the imported repo."""

    def __init__(self):
        self.done = False

    def check(self, ping_result: Optional[dict]) -> Optional[bool]:
        if not ping_result:
            return None
        script_version = ping_result.get("version")
        script_hash = ping_result.get("schema_hash")
        in_sync = script_version == VERSION and script_hash == REGISTRY.schema_hash()
        if not in_sync and not self.done:
            logger.warning(
                "Control surface inside Live (v%s) does not match this package (v%s). "
                "Re-run scripts/install_control_surface.py and restart Live.",
                script_version,
                VERSION,
            )
        self.done = True
        return in_sync


def _bridge_status(client: AbletonClient, drift: _DriftCheck) -> dict:
    ping_result = client.ping()
    if not ping_result:
        return {
            "connected": False,
            "package_version": VERSION,
            "hint": (
                "Start Ableton Live and enable the AbletonMCP control surface under "
                "Preferences > Link, Tempo & MIDI. If it is enabled, re-run "
                "scripts/install_control_surface.py and restart Live."
            ),
        }
    in_sync = drift.check(ping_result)
    return {
        "connected": True,
        "package_version": VERSION,
        "script_version": ping_result.get("version", "unknown"),
        "schema_in_sync": bool(in_sync),
    }


def build_server(client: Optional[AbletonClient] = None) -> Server:
    ableton = client if client is not None else AbletonClient()
    drift = _DriftCheck()
    server = Server("ableton-mcp", version=VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return registry_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict:
        if name == "get_bridge_status":
            return await anyio.to_thread.run_sync(_bridge_status, ableton, drift)

        if name not in REGISTRY:
            raise ValueError(f"Unknown tool: {name}")

        def _dispatch() -> Any:
            if not drift.done:
                drift.check(ableton.ping())
            return ableton.send(name, **arguments)

        try:
            result = await anyio.to_thread.run_sync(_dispatch)
        except AbletonConnectionError as e:
            raise RuntimeError(f"Ableton is not reachable: {e}") from e
        except CommandError as e:
            raise RuntimeError(f"Ableton rejected the command: {e}") from e

        # A dict return produces structuredContent plus serialized text content.
        if isinstance(result, dict):
            return result
        return {"result": result}

    return server


def summarize_tools() -> str:
    """For diagnostics: names only."""
    return json.dumps([t.name for t in registry_tools()], indent=2)
