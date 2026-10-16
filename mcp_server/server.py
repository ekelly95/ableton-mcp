"""MCP server: exposes the control surface registry as MCP tools.

Tools are generated from control_surface's REGISTRY — imported directly, the
single source of truth. Nothing here defines a tool schema by hand except the
tools that cannot or need not run inside Live: get_bridge_status (reports
whether Live is reachable), get_audio_levels (mcp_server/m4l.py, the optional
tap), transform_clip (server-side composite), and search_library
(mcp_server/library.py — reads Live's database files, never Live itself).

All logging goes to stderr: stdout belongs to the MCP stdio transport.
"""

import json
import logging
import sys
from typing import Any

import anyio.to_thread
import jsonschema
import mcp.types as types
from mcp.server.caching import CacheHint
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
from .library import search_library as _search_library  # noqa: E402
from .m4l import AUDIO_LEVELS_TOOL, TapClient, get_audio_levels  # noqa: E402
from .notation import parse_notation, serialize_notation  # noqa: E402
from .transforms import TRANSFORM_FIELD_DEFAULTS, apply_transforms  # noqa: E402

BRIDGE_STATUS_TOOL = types.Tool(
    name="get_bridge_status",
    description=(
        "Health check for the Ableton bridge: is Live reachable, do versions and "
        "schemas match. Call when other tools fail with connection errors."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
    output_schema={
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
        "> 100): v-15; C1 1|1-2|*: ratchet(n/16)'. Refuses clips over the "
        "2000-note read limit rather than silently editing only the first "
        "2000."
    ),
    input_schema={
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
    annotations=types.ToolAnnotations(read_only_hint=False, destructive_hint=True),
)

SEARCH_LIBRARY_TOOL = types.Tool(
    name="search_library",
    description=(
        "Search Live's own library database (works even while Live is closed): "
        "samples, presets/racks, clips, MIDI files, grooves, Sets, plug-ins. "
        "Filter by name substring, Live tags, kind, source; sorted by how often "
        "each item has been used. Give at least one filter — or similar_to, "
        "which instead ranks every analyzed file by how alike it sounds (Live's "
        "own audio analysis; 'more 808s like this one'). To load a hit: "
        "browse/load_item with its browser_path_guess when present; for samples "
        "import_audio with its absolute path always works. Results reflect "
        "Live's last database snapshot (a staleness field says when that lags)."
    ),
    input_schema={
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
            "similar_to": {
                "type": "string",
                "description": (
                    "Reference sound: absolute path, or a name substring (best-used "
                    "match). Not combinable with the other filters; limit max 50."
                ),
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
    annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
    output_schema={
        "type": "object",
        "properties": {
            "matches": {"type": "array", "items": {"type": "object"}},
            "truncated": {"type": "boolean"},
            "staleness": {"type": "string"},
            "reference": {"type": "object"},
            "note": {"type": "string"},
        },
        "required": ["matches"],
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


def _transform_clip(ableton: AbletonClient, arguments: dict[str, Any]) -> dict:
    """get_notes -> apply_transforms -> write back the diff by note_id."""
    clip_ref = {
        key: arguments[key]
        for key in ("track_index", "slot_index", "arrangement_clip_index", "take_lane_index")
        if key in arguments
    }
    read = ableton.send("get_notes", **clip_ref)
    if read.get("truncated"):
        # Refuse BEFORE any write: transforming only the readable prefix would
        # silently leave the rest of the clip untouched and report the capped
        # count as if it were the whole clip.
        raise ValueError(
            f"Clip exceeds the {len(read['notes'])}-note read limit, so this "
            "transform would silently edit only the first "
            f"{len(read['notes'])} notes. Split the clip or transform a "
            "smaller region instead."
        )
    transport = ableton.send("get_transport_state")
    transformed, matched, warnings = apply_transforms(
        read["notes"],
        arguments["transforms"],
        int(transport.get("signature_numerator", 4)),
        int(transport.get("signature_denominator", 4)),
        clip_duration=read.get("clip_length"),
        seed=arguments.get("seed"),
        clip_start=read.get("clip_start", 0.0),
    )

    def field_value(note: dict, field: str) -> Any:
        return note.get(field, TRANSFORM_FIELD_DEFAULTS.get(field))

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

    if removed_ids or modifications or added:
        # One all-or-nothing command: Live refuses it if the clip changed
        # since the read above, and validates everything before writing.
        ableton.send(
            "apply_note_diff",
            **clip_ref,
            expected_note_ids=list(original_by_id),
            remove_ids=removed_ids,
            modifications=modifications,
            notes=added,
        )

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
                input_schema=spec["inputSchema"],
                annotations=types.ToolAnnotations(
                    read_only_hint=spec["annotations"]["readOnlyHint"],
                    destructive_hint=spec["annotations"]["destructiveHint"],
                ),
                output_schema=spec.get("outputSchema"),
            )
        )
    tools.append(BRIDGE_STATUS_TOOL)
    tools.append(AUDIO_LEVELS_TOOL)  # always listed; answers available:false without the tap
    tools.append(TRANSFORM_CLIP_TOOL)  # server-side composite: outside the registry hash
    # Library intelligence reads Live's database files, never Live itself —
    # also outside the registry hash.
    tools.append(SEARCH_LIBRARY_TOOL)
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


def _tool_result(result: Any) -> types.CallToolResult:
    """Compact text + the same payload as structured content.

    The text copy is what the model actually reads, so it is compact JSON (an
    indent=2 copy measured 1.5-1.9x the tokens); the structured copy is for
    output_schema and typed clients.
    """
    payload = result if isinstance(result, dict) else {"result": result}
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], structured_content=payload
    )


def _tool_error(message: str) -> types.CallToolResult:
    """An error the model gets to read. mcp 2 turns an exception raised from a
    handler into a bare "Internal server error", so every failure the model
    should see — Live unreachable, a refused command, bad arguments — is
    returned this way instead."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


def _argument_validators(tools: list[types.Tool]) -> dict[str, Any]:
    """jsonschema validators per tool. mcp 2 no longer checks arguments against
    input_schema, and the server-only tools (transform_clip, the library
    tools, get_audio_levels) have no Live-side validation behind them."""
    validators = {}
    for tool in tools:
        cls = jsonschema.validators.validator_for(tool.input_schema)
        validators[tool.name] = cls(tool.input_schema)
    return validators


def _argument_error(name: str, validator: Any, arguments: dict[str, Any]) -> str | None:
    error = jsonschema.exceptions.best_match(validator.iter_errors(arguments))
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path)
    return f"Invalid arguments for {name}{f' ({where})' if where else ''}: {error.message}"


def build_server(client: AbletonClient | None = None, tap: TapClient | None = None) -> Server:
    ableton = client if client is not None else AbletonClient.from_env()
    tap_client = tap if tap is not None else TapClient.from_env()
    drift = _DriftCheck()
    tools = registry_tools()
    validators = _argument_validators(tools)

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    def _signature() -> tuple[int, int]:
        transport = ableton.send("get_transport_state")
        return (
            int(transport.get("signature_numerator", 4)),
            int(transport.get("signature_denominator", 4)),
        )

    def _dispatch(name: str, arguments: dict[str, Any]) -> Any:
        """Runs in a worker thread: every path below may block on Live or disk."""
        if name == "get_bridge_status":
            return _bridge_status(ableton, drift)
        if name == "get_audio_levels":
            return get_audio_levels(tap_client, float(arguments.get("duration_seconds", 0) or 0))
        if name == "transform_clip":
            return _transform_clip(ableton, arguments)
        # Library tools read Live's database files directly, never Live.
        if name == "search_library":
            return _search_library(arguments)

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

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        validator = validators.get(name)
        if validator is None:
            # Unlisted names, including internal registry commands, stop here.
            return _tool_error(f"Unknown tool: {name}")
        problem = _argument_error(name, validator, arguments)
        if problem:
            return _tool_error(problem)
        try:
            result = await anyio.to_thread.run_sync(_dispatch, name, arguments)
        except AbletonConnectionError as e:
            return _tool_error(f"Ableton is not reachable: {e}")
        except CommandError as e:
            return _tool_error(f"Ableton rejected the command: {e}")
        except Exception as e:
            # ValueError/TransformError from the server-side dialects, and
            # anything unexpected: the model still gets the message.
            logger.exception("Tool %s failed", name)
            return _tool_error(f"{name} failed: {e}")
        return _tool_result(result)

    # This server has no resources or prompts. Codex probes for them anyway
    # and treats the correct -32601 "no such method" reply as the server
    # failing to start (openai/codex#37468). Empty handlers cost nothing and
    # keep it quiet. Do not delete these as dead code.
    async def list_resources(ctx, params) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=[])

    async def list_resource_templates(ctx, params) -> types.ListResourceTemplatesResult:
        return types.ListResourceTemplatesResult(resource_templates=[])

    async def list_prompts(ctx, params) -> types.ListPromptsResult:
        return types.ListPromptsResult(prompts=[])

    # The tool list is fixed for the life of this process (a new version means
    # a restart), so clients on the 2026-07-28 protocol may cache the lists.
    static = CacheHint(ttl_ms=60 * 60 * 1000)
    return Server(
        "ableton-mcp",
        version=VERSION,
        cache_hints={
            "tools/list": static,
            "resources/list": static,
            "resources/templates/list": static,
            "prompts/list": static,
        },
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        on_list_resources=list_resources,
        on_list_resource_templates=list_resource_templates,
        on_list_prompts=list_prompts,
    )
