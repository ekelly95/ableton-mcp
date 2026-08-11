"""Command registry: the single source of truth for the bridge's command surface.

Every command self-registers here with typed parameter schemas, an optional
output schema, and read-only/destructive flags. The MCP server imports this
registry directly and generates its tools from it — there is deliberately no
second place where tool definitions live (the 1.0 rebuild died of exactly
that duplication).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .config import COMMAND_TIMEOUTS

# LiveAPIError is re-exported on purpose (the `as` form marks it intentional —
# plain imports get stripped as unused): command modules import both errors
# from the registry, their historical home.
from .errors import LiveAPIError as LiveAPIError
from .errors import ValidationError as ValidationError
from .log import get_logger
from .utils.pitch import pitch_to_midi

logger = get_logger("registry")

# Modern note-ID API note fields (Live 11.1+). Defaults follow Live's own.
NOTE_FIELD_DEFAULTS = {
    "velocity": 100.0,
    "mute": False,
    "probability": 1.0,
    "velocity_deviation": 0.0,
    "release_velocity": 64.0,
}


class ParamType(Enum):
    INT = "integer"
    FLOAT = "number"
    STRING = "string"
    BOOL = "boolean"
    INT_LIST = "int_array"
    FLOAT_LIST = "float_array"
    STRING_LIST = "string_array"
    NOTE = "note"
    NOTE_LIST = "note_list"
    OBJECT = "object"
    OBJECT_LIST = "object_list"
    ANY = "any"


NOTE_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pitch": {
            "anyOf": [
                {"type": "integer", "minimum": 0, "maximum": 127},
                {"type": "string"},
            ],
            "description": (
                "MIDI number 0-127 OR a name like 'C3', 'F#4', 'Bb2'. "
                "ABLETON convention: C3 = 60 (not C4)."
            ),
        },
        "start_time": {"type": "number", "minimum": 0, "description": "In beats"},
        "duration": {"type": "number", "exclusiveMinimum": 0, "description": "In beats"},
        "velocity": {"type": "number", "minimum": 0, "maximum": 127, "default": 100},
        "mute": {"type": "boolean", "default": False},
        "probability": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "default": 1.0,
            "description": "Chance the note plays (Live 11+ probability)",
        },
        "velocity_deviation": {
            "type": "number",
            "minimum": -127,
            "maximum": 127,
            "default": 0,
            "description": "Random velocity range added per play",
        },
        "release_velocity": {
            "type": "number",
            "minimum": 0,
            "maximum": 127,
            "default": 64,
        },
    },
    "required": ["pitch", "start_time", "duration"],
}

# to_json_schema copies its entry shallowly and only adds top-level keys, so
# sharing the nested dicts here is safe.
_JSON_TYPE_MAP: dict[ParamType, dict[str, Any]] = {
    ParamType.INT: {"type": "integer"},
    ParamType.FLOAT: {"type": "number"},
    ParamType.STRING: {"type": "string"},
    ParamType.BOOL: {"type": "boolean"},
    ParamType.INT_LIST: {"type": "array", "items": {"type": "integer"}},
    ParamType.FLOAT_LIST: {"type": "array", "items": {"type": "number"}},
    ParamType.STRING_LIST: {"type": "array", "items": {"type": "string"}},
    ParamType.NOTE: NOTE_OBJECT_SCHEMA,
    ParamType.NOTE_LIST: {"type": "array", "items": NOTE_OBJECT_SCHEMA},
    ParamType.OBJECT: {"type": "object"},
    ParamType.OBJECT_LIST: {"type": "array", "items": {"type": "object"}},
    ParamType.ANY: {},
}


@dataclass
class ParamSchema:
    name: str
    param_type: ParamType
    required: bool = True
    default: Any = None
    min_value: float | None = None
    max_value: float | None = None
    description: str = ""
    enum_values: list[Any] | None = None
    # JSON Schema for items of OBJECT / OBJECT_LIST params; documentation plus
    # shallow shape checking (dict-ness); deep validation stays in handlers.
    item_schema: dict[str, Any] | None = None

    def validate(self, value: Any) -> Any:
        if value is None:
            if self.required:
                raise ValidationError("Required parameter missing", param=self.name)
            return self.default

        try:
            validated = self._validate_type(value)
        except (TypeError, ValueError):
            raise ValidationError(
                f"Invalid type: expected {self.param_type.value}, got {type(value).__name__}",
                param=self.name,
                value=value,
            ) from None

        if self.param_type in (ParamType.INT, ParamType.FLOAT):
            if self.min_value is not None and validated < self.min_value:
                raise ValidationError(
                    f"Value {validated} is below minimum {self.min_value}",
                    param=self.name,
                    value=value,
                )
            if self.max_value is not None and validated > self.max_value:
                raise ValidationError(
                    f"Value {validated} is above maximum {self.max_value}",
                    param=self.name,
                    value=value,
                )

        if self.enum_values is not None and validated not in self.enum_values:
            raise ValidationError(
                f"Value must be one of: {self.enum_values}", param=self.name, value=value
            )

        return validated

    def _validate_type(self, value: Any) -> Any:
        pt = self.param_type
        if pt == ParamType.INT:
            if isinstance(value, bool):
                raise TypeError("Boolean not allowed for integer")
            return int(value)
        if pt == ParamType.FLOAT:
            if isinstance(value, bool):
                raise TypeError("Boolean not allowed for float")
            return float(value)
        if pt == ParamType.STRING:
            if not isinstance(value, str):
                raise TypeError("Expected string")
            return value
        if pt == ParamType.BOOL:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                low = value.lower()
                if low in ("true", "1", "yes"):
                    return True
                if low in ("false", "0", "no"):
                    return False
            if isinstance(value, (int, float)):
                return bool(value)
            raise TypeError(f"Cannot convert {type(value).__name__} to bool")
        if pt == ParamType.INT_LIST:
            if not isinstance(value, (list, tuple)):
                raise TypeError("Expected list")
            return [int(v) for v in value]
        if pt == ParamType.FLOAT_LIST:
            if not isinstance(value, (list, tuple)):
                raise TypeError("Expected list")
            return [float(v) for v in value]
        if pt == ParamType.STRING_LIST:
            if not isinstance(value, (list, tuple)):
                raise TypeError("Expected list")
            return [str(v) for v in value]
        if pt == ParamType.NOTE:
            return self._validate_note(value)
        if pt == ParamType.NOTE_LIST:
            if not isinstance(value, (list, tuple)):
                raise TypeError("Expected list of notes")
            return [self._validate_note(n) for n in value]
        if pt == ParamType.OBJECT:
            if not isinstance(value, dict):
                raise TypeError("Expected object")
            return value
        if pt == ParamType.OBJECT_LIST:
            if not isinstance(value, (list, tuple)):
                raise TypeError("Expected list of objects")
            for v in value:
                if not isinstance(v, dict):
                    raise TypeError("Expected list of objects")
            return list(value)
        if pt == ParamType.ANY:
            return value
        raise TypeError(f"Unknown param type: {pt}")

    def _validate_note(self, note: Any) -> dict[str, Any]:
        if not isinstance(note, dict):
            raise TypeError("Note must be a dictionary")

        for required_field in ("pitch", "start_time", "duration"):
            if note.get(required_field) is None:
                raise ValidationError(f"Note missing '{required_field}'", param=self.name)

        validated = {
            "pitch": pitch_to_midi(note["pitch"], param=self.name),
            "start_time": float(note["start_time"]),
            "duration": float(note["duration"]),
            "velocity": float(note.get("velocity", NOTE_FIELD_DEFAULTS["velocity"])),
            "mute": bool(note.get("mute", NOTE_FIELD_DEFAULTS["mute"])),
            "probability": float(note.get("probability", NOTE_FIELD_DEFAULTS["probability"])),
            "velocity_deviation": float(
                note.get("velocity_deviation", NOTE_FIELD_DEFAULTS["velocity_deviation"])
            ),
            "release_velocity": float(
                note.get("release_velocity", NOTE_FIELD_DEFAULTS["release_velocity"])
            ),
        }

        if not 0 <= validated["pitch"] <= 127:
            raise ValidationError(f"Pitch must be 0-127, got {validated['pitch']}", param=self.name)
        if not 0 <= validated["velocity"] <= 127:
            raise ValidationError(
                f"Velocity must be 0-127, got {validated['velocity']}", param=self.name
            )
        if not 0 <= validated["release_velocity"] <= 127:
            raise ValidationError(
                f"Release velocity must be 0-127, got {validated['release_velocity']}",
                param=self.name,
            )
        if not 0.0 <= validated["probability"] <= 1.0:
            raise ValidationError(
                f"Probability must be 0.0-1.0, got {validated['probability']}", param=self.name
            )
        if not -127 <= validated["velocity_deviation"] <= 127:
            raise ValidationError(
                f"Velocity deviation must be -127..127, got {validated['velocity_deviation']}",
                param=self.name,
            )
        if validated["duration"] <= 0:
            raise ValidationError(
                f"Duration must be positive, got {validated['duration']}", param=self.name
            )
        if validated["start_time"] < 0:
            raise ValidationError(
                f"Start time must be non-negative, got {validated['start_time']}", param=self.name
            )

        return validated

    def to_json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = dict(_JSON_TYPE_MAP.get(self.param_type, {}))

        if self.item_schema is not None:
            if self.param_type == ParamType.OBJECT_LIST:
                schema["items"] = self.item_schema
            elif self.param_type == ParamType.OBJECT:
                schema = dict(self.item_schema)

        if self.description:
            schema["description"] = self.description
        if self.min_value is not None and self.param_type in (ParamType.INT, ParamType.FLOAT):
            schema["minimum"] = self.min_value
        if self.max_value is not None and self.param_type in (ParamType.INT, ParamType.FLOAT):
            schema["maximum"] = self.max_value
        if self.enum_values is not None:
            schema["enum"] = self.enum_values
        if self.default is not None:
            schema["default"] = self.default

        return schema


@dataclass
class CommandSchema:
    name: str
    handler: Callable
    params: list[ParamSchema] = field(default_factory=list)
    timeout: float | None = None
    description: str = ""
    category: str = "general"
    read_only: bool = False
    destructive: bool = False
    output_schema: dict[str, Any] | None = None

    def validate_params(self, params: dict[str, Any] | None) -> dict[str, Any]:
        if params is None:
            params = {}

        validated = {}
        for param_schema in self.params:
            validated[param_schema.name] = param_schema.validate(params.get(param_schema.name))

        unknown = set(params.keys()) - {p.name for p in self.params}
        if unknown:
            logger.warning(f"Unknown parameters for {self.name}: {unknown}")

        return validated

    def to_mcp_tool(self) -> dict[str, Any]:
        properties = {}
        required = []
        for param in self.params:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        input_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            input_schema["required"] = required

        tool: dict[str, Any] = {
            "name": self.name,
            "description": self.description or f"Execute {self.name}",
            "inputSchema": input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
            },
        }
        if self.output_schema is not None:
            tool["outputSchema"] = self.output_schema

        return tool


class CommandRegistry:
    def __init__(self):
        self._commands: dict[str, CommandSchema] = {}
        self._categories: dict[str, list[str]] = {}

    def register(
        self,
        name: str,
        params: list[ParamSchema] | None = None,
        timeout: float | None = None,
        description: str = "",
        category: str = "general",
        read_only: bool = False,
        destructive: bool = False,
        output_schema: dict[str, Any] | None = None,
    ) -> Callable[[Callable], Callable]:
        def decorator(handler: Callable) -> Callable:
            if name in self._commands:
                raise ValueError(f"Command '{name}' is already registered")

            self._commands[name] = CommandSchema(
                name=name,
                handler=handler,
                params=params or [],
                timeout=timeout if timeout is not None else COMMAND_TIMEOUTS.get(name),
                description=description,
                category=category,
                read_only=read_only,
                destructive=destructive,
                output_schema=output_schema,
            )
            self._categories.setdefault(category, []).append(name)
            return handler

        return decorator

    def get(self, name: str) -> CommandSchema | None:
        return self._commands.get(name)

    def list_commands(self) -> list[str]:
        return list(self._commands.keys())

    def get_categories(self) -> list[str]:
        return list(self._categories.keys())

    def list_by_category(self, category: str) -> list[str]:
        return list(self._categories.get(category, []))

    def generate_mcp_tools(self) -> list[dict[str, Any]]:
        return [schema.to_mcp_tool() for schema in self._commands.values()]

    def schema_hash(self) -> str:
        """Stable hash of the full tool surface, used to detect drift between
        the repo the MCP server imports and the copy deployed inside Live."""
        canonical = json.dumps(self.generate_mcp_tools(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def __len__(self) -> int:
        return len(self._commands)

    def __contains__(self, name: str) -> bool:
        return name in self._commands


REGISTRY = CommandRegistry()
