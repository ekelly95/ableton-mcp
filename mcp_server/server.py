"""MCP server: exposes the control surface registry as MCP tools.

Tools are generated from control_surface's REGISTRY — imported directly, the
single source of truth. Nothing here defines a tool schema by hand except the
two tools that cannot run inside Live: get_bridge_status (reports whether Live
is reachable) and get_audio_levels (mcp_server/m4l.py, the optional tap).

All logging goes to stderr: stdout belongs to the MCP stdio transport.
"""

import logging
import sys
from typing import Any

import anyio.to_thread
import mcp.types as types
from mcp.server.lowlevel import Server

# stderr-only logging BEFORE the control_surface import chain runs.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("ableton-mcp")

from control_surface.commands import REGISTRY  # noqa: E402 - after logging setup
from control_surface.config import VERSION  # noqa: E402

from .client import (  # noqa: E402
    NOT_RUNNING_HINT,
    AbletonClient,
    AbletonConnectionError,
    CommandError,
)
from .m4l import AUDIO_LEVELS_TOOL, TapClient, get_audio_levels  # noqa: E402

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
    tools.append(AUDIO_LEVELS_TOOL)  # always listed; answers available:false without the tap
    return tools


class _DriftCheck:
    """Warn once if the copy deployed inside Live differs from the imported repo."""

    def __init__(self):
        self.done = False

    def check(self, ping_result: dict | None) -> bool | None:
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
                f"{NOT_RUNNING_HINT} If it is enabled, re-run "
                f"scripts/install_control_surface.py and restart Live."
            ),
        }
    in_sync = drift.check(ping_result)
    return {
        "connected": True,
        "package_version": VERSION,
        "script_version": ping_result.get("version", "unknown"),
        "schema_in_sync": bool(in_sync),
    }


def build_server(client: AbletonClient | None = None, tap: TapClient | None = None) -> Server:
    ableton = client if client is not None else AbletonClient()
    tap_client = tap if tap is not None else TapClient()
    drift = _DriftCheck()
    server = Server("ableton-mcp", version=VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return registry_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> dict:
        if name == "get_bridge_status":
            return await anyio.to_thread.run_sync(_bridge_status, ableton, drift)

        if name == "get_audio_levels":
            duration = float(arguments.get("duration_seconds", 0) or 0)
            return await anyio.to_thread.run_sync(get_audio_levels, tap_client, duration)

        if name not in REGISTRY:
            raise ValueError(f"Unknown tool: {name}")

        def _dispatch() -> Any:
            if not drift.done:
                drift.check(ableton.ping())
            # Resolves two-phase seeks so the model sees a single tool call.
            return ableton.send_resolving_seek(name, **arguments)

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

    # This server has no resources or prompts, and initialize says so. Codex
    # probes for them anyway and treats the correct -32601 "no such method"
    # reply as the server failing to start (openai/codex#37468, still open in
    # 0.147.0). Empty handlers cost nothing and keep it quiet. Do not delete
    # these as dead code.
    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return []

    @server.list_resource_templates()
    async def list_resource_templates() -> list[types.ResourceTemplate]:
        return []

    @server.list_prompts()
    async def list_prompts() -> list[types.Prompt]:
        return []

    return server
