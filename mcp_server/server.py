"""MCP server: exposes the control surface registry as MCP tools.

Tools are generated from control_surface's REGISTRY — imported directly, the
single source of truth. Nothing here defines a tool schema by hand except the
tools that cannot or need not run inside Live: get_bridge_status (reports
whether Live is reachable), get_audio_levels (mcp_server/m4l.py, the optional
tap), transform_clip (server-side composite), and search_library/find_similar
(mcp_server/library.py — read Live's database files, never Live itself).

All logging goes to stderr: stdout belongs to the MCP stdio transport.
"""

import json
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
from .library import find_similar as _find_similar  # noqa: E402
from .library import search_library as _search_library  # noqa: E402
from .m4l import AUDIO_LEVELS_TOOL, TapClient, get_audio_levels  # noqa: E402
from .notation import parse_notation, serialize_notation  # noqa: E402
from .transforms import apply_transforms  # noqa: E402

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


TRANSFORM_CLIP_TOOL = types.Tool(
    name="transform_clip",
    description=(
        "Reshape a clip's EXISTING notes with transform statements — the notes "
        "never enter the conversation. Statements are ';'-separated: "
        "[selectors ':'] action. Selectors: pitch (C1 or C1-C3), time (2|1, "
        "1|1-3|4, 1|1-<3|1 exclusive, 3|* whole bar), where(note.velocity > 80). "
        "Actions: velocity/pitch/timing/duration/probability/deviation with "
        "= += -= *= /=; shorthands v90 v90-110 v+10 p0.8 n/8, v0 deletes; note "
        "ops ratchet(4|n/16) repeat(n/8[,count]) merge([gap]). Value functions: "
        "sin/cos/tri/saw/square(period), ramp(a,b), swing([amt]), quant(grid"
        "[,strength]), legato([tol]), snap(C,Eb,...), rand(), choose(...), "
        "clamp/round/floor/ceil/abs/min/max/pow. Times/durations in n/X or "
        "Nbar units. Example: 'F#1: timing = swing(0.57); where(note.velocity "
        "> 100): v-15; C1 1|1-2|*: ratchet(n/16)'"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "track_index": {"type": "integer", "minimum": 0},
            "slot_index": {"type": "integer", "minimum": 0},
            "arrangement_clip_index": {"type": "integer", "minimum": 0},
            "take_lane_index": {
                "type": "integer",
                "minimum": 0,
                "description": "With arrangement_clip_index: address a clip inside this take lane",
            },
            "transforms": {"type": "string"},
            "seed": {
                "type": "integer",
                "description": "Seeds rand()/choose() for reproducible results",
            },
        },
        "required": ["track_index", "transforms"],
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=True),
)

SEARCH_LIBRARY_TOOL = types.Tool(
    name="search_library",
    description=(
        "Search Live's own library database (works even while Live is closed): "
        "samples, presets/racks, clips, MIDI files, grooves, Sets, plug-ins. "
        "Filter by name substring, Live tags, kind, source; sorted by how often "
        "each item has been used. Give at least one filter. To load a hit: "
        "browse/load_item with its browser_path_guess when present; for samples "
        "import_audio with its absolute path always works. Results reflect "
        "Live's last database snapshot (a staleness field says when that lags)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Case-insensitive substring of the item name, e.g. '808'",
            },
            "tags": {
                "type": "string",
                "description": (
                    "Comma-separated Live tags that must ALL match, e.g. "
                    "'Kick,Punchy'. Hits list their tags — mine those for the "
                    "vocabulary."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["sample", "preset", "clip", "midi", "set", "groove", "plugin", "any"],
                "description": "preset covers .adv/.adg/.amxd; default any",
            },
            "source": {
                "type": "string",
                "enum": [
                    "user_library",
                    "core_library",
                    "builtin",
                    "current_project",
                    "cloud",
                    "any",
                ],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    outputSchema={
        "type": "object",
        "properties": {
            "matches": {"type": "array", "items": {"type": "object"}},
            "truncated": {"type": "boolean"},
            "staleness": {"type": "string"},
        },
        "required": ["matches"],
    },
)

FIND_SIMILAR_TOOL = types.Tool(
    name="find_similar",
    description=(
        "Rank library files by sonic similarity to a reference, using Live's "
        "own per-file audio analysis (64-dim feature vectors — 'more sounds "
        "like this 808'). Reference: exactly one of path (absolute) or query "
        "(name substring; best-used match wins). Only files Live has analyzed "
        "appear. Load hits like search_library hits."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path of the reference file"},
            "query": {"type": "string", "description": "Name substring of the reference"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    outputSchema={
        "type": "object",
        "properties": {
            "reference": {"type": "object"},
            "matches": {"type": "array", "items": {"type": "object"}},
            "note": {"type": "string"},
            "staleness": {"type": "string"},
        },
        "required": ["reference", "matches"],
    },
)


# Fields transform statements may change; the write-back diff compares these.
_TRANSFORM_FIELDS = (
    "pitch",
    "start_time",
    "duration",
    "velocity",
    "probability",
    "velocity_deviation",
)
_TRANSFORM_FIELD_DEFAULTS = {"probability": 1.0, "velocity_deviation": 0.0}


def _transform_clip(ableton: AbletonClient, arguments: dict[str, Any]) -> dict:
    """get_notes -> apply_transforms -> write back the diff by note_id."""
    clip_ref = {
        key: arguments[key]
        for key in ("track_index", "slot_index", "arrangement_clip_index", "take_lane_index")
        if key in arguments
    }
    read = ableton.send("get_notes", **clip_ref)
    transport = ableton.send("get_transport_state")
    transformed, matched, warnings = apply_transforms(
        read["notes"],
        arguments["transforms"],
        int(transport.get("signature_numerator", 4)),
        int(transport.get("signature_denominator", 4)),
        clip_duration=read.get("clip_length"),
        seed=arguments.get("seed"),
    )

    def field_value(note: dict, field: str) -> Any:
        return note.get(field, _TRANSFORM_FIELD_DEFAULTS.get(field))

    original_by_id = {n["note_id"]: n for n in read["notes"]}
    surviving_ids = {n["note_id"] for n in transformed if "note_id" in n}
    removed_ids = [nid for nid in original_by_id if nid not in surviving_ids]
    modifications = []
    added = []
    for note in transformed:
        if "note_id" not in note:
            added.append({k: v for k, v in note.items() if k in _TRANSFORM_FIELDS})
            continue
        original = original_by_id[note["note_id"]]
        changed = {
            field: field_value(note, field)
            for field in _TRANSFORM_FIELDS
            if field_value(note, field) is not None
            and field_value(note, field) != field_value(original, field)
        }
        if changed:
            modifications.append({"note_id": note["note_id"], **changed})

    if removed_ids:
        ableton.send("remove_notes", **clip_ref, note_ids=removed_ids)
    if modifications:
        ableton.send("update_notes", **clip_ref, modifications=modifications)
    if added:
        ableton.send("add_notes", **clip_ref, notes=added)

    result = {
        "matched": matched,
        "updated": len(modifications),
        "removed": len(removed_ids),
        "added": len(added),
        "note_count": len(transformed),
    }
    if warnings:
        result["warnings"] = warnings
    return result


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
    tools.append(TRANSFORM_CLIP_TOOL)  # server-side composite: outside the registry hash
    # Library intelligence reads Live's database files, never Live itself —
    # also outside the registry hash.
    tools.append(SEARCH_LIBRARY_TOOL)
    tools.append(FIND_SIMILAR_TOOL)
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


def _tool_result(result: Any) -> tuple[list[types.TextContent], dict]:
    """Compact text + structured dict, replacing the SDK's default emission.

    A bare dict return makes the SDK serialize the SAME payload twice, the text
    copy with indent=2 — a measured 1.5-1.9x token multiplier on every result.
    The text copy is what the model actually reads, so it must be compact; the
    structured copy stays for output_schema validation and typed clients.
    """
    payload = result if isinstance(result, dict) else {"result": result}
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return [types.TextContent(type="text", text=text)], payload


def build_server(client: AbletonClient | None = None, tap: TapClient | None = None) -> Server:
    ableton = client if client is not None else AbletonClient()
    tap_client = tap if tap is not None else TapClient()
    drift = _DriftCheck()
    server = Server("ableton-mcp", version=VERSION)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return registry_tools()

    @server.call_tool()
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> tuple[list[types.TextContent], dict]:
        if name == "get_bridge_status":
            return _tool_result(await anyio.to_thread.run_sync(_bridge_status, ableton, drift))

        if name == "get_audio_levels":
            duration = float(arguments.get("duration_seconds", 0) or 0)
            return _tool_result(
                await anyio.to_thread.run_sync(get_audio_levels, tap_client, duration)
            )

        if name == "transform_clip":
            try:
                return _tool_result(
                    await anyio.to_thread.run_sync(_transform_clip, ableton, arguments)
                )
            except AbletonConnectionError as e:
                raise RuntimeError(f"Ableton is not reachable: {e}") from e
            except CommandError as e:
                raise RuntimeError(f"Ableton rejected the command: {e}") from e

        # Library tools read the database files directly — no Live, no
        # connection errors to translate; sqlite is blocking I/O, so thread it.
        if name == "search_library":
            return _tool_result(await anyio.to_thread.run_sync(_search_library, arguments))

        if name == "find_similar":
            return _tool_result(await anyio.to_thread.run_sync(_find_similar, arguments))

        if name not in REGISTRY:
            raise ValueError(f"Unknown tool: {name}")

        def _signature() -> tuple[int, int]:
            transport = ableton.send("get_transport_state")
            return (
                int(transport.get("signature_numerator", 4)),
                int(transport.get("signature_denominator", 4)),
            )

        def _dispatch() -> Any:
            if not drift.done:
                drift.check(ableton.ping())
            args = dict(arguments)

            # Notation and transforms are server-side dialects: Live only ever
            # sees note dicts.
            if name == "add_notes" and (args.get("notation") or args.get("transforms")):
                warnings: list[str] = []
                num, den = _signature()
                if args.get("notation"):
                    if args.get("notes"):
                        raise ValueError("Pass either 'notes' or 'notation', not both")
                    notes, warnings = parse_notation(args.pop("notation"), num, den)
                else:
                    notes = args.get("notes") or []
                if args.get("transforms"):
                    notes, _, transform_warnings = apply_transforms(
                        notes, args.pop("transforms"), num, den
                    )
                    warnings = warnings + transform_warnings
                if not notes:
                    raise ValueError(
                        "No notes left to add after notation/transforms: "
                        + ("; ".join(warnings) or "empty input")
                    )
                args["notes"] = notes
                args.pop("transforms", None)
                result = ableton.send_resolving_seek(name, **args)
                if warnings and isinstance(result, dict):
                    result["notation_warnings"] = warnings
                return result

            if name == "get_notes" and args.pop("format", "json") == "compact":
                result = ableton.send_resolving_seek(name, **args)
                if isinstance(result, dict) and "notes" in result:
                    num, den = _signature()
                    result["notation"] = serialize_notation(result.pop("notes"), num, den)
                return result

            # Resolves two-phase seeks so the model sees a single tool call.
            return ableton.send_resolving_seek(name, **args)

        try:
            result = await anyio.to_thread.run_sync(_dispatch)
        except AbletonConnectionError as e:
            raise RuntimeError(f"Ableton is not reachable: {e}") from e
        except CommandError as e:
            raise RuntimeError(f"Ableton rejected the command: {e}") from e

        return _tool_result(result)

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
