"""Devices: inspect, batch parameter setting (normalized 0-1), delete.

Deep native-device control (2.7): some device state never appears in
device.parameters — Simpler's playback mode, EQ Eight's M/S switch, Drift's
mod matrix live as CLASS-LEVEL properties in the LOM. _CLASS_PROPS maps them
per device class; get_devices emits a class_properties block when it knows
the device, and set_device_parameters accepts those names (and safe class
methods via `invoke`) transparently.
"""

from typing import Any

from ..errors import batch_writer
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_device, resolve_device_parameter, resolve_track
from ..utils.normalize import denormalize_parameter, normalize_parameter

# LOM DeviceParameter.automation_state values.
_AUTOMATION_STATES = {0: "none", 1: "active", 2: "overridden"}

# Shared by every command that addresses a device chain (2.8): regular
# tracks, return tracks, and the Main track all carry devices. 'master'
# ignores track_index; 'return' indexes song.return_tracks.
TRACK_TYPE_PARAM = ParamSchema(
    "track_type",
    ParamType.STRING,
    required=False,
    default="track",
    enum_values=["track", "return", "master"],
    description="Which list track_index addresses; 'master' needs no track_index",
)

# --- Class-level property tables (2.7) ---------------------------------------
# Data from the Live 12.3.5 LOM docs (docs.cycling74.com/apiref/lom/); every
# entry is VERIFY until the 2.7 checkpoint confirms it on real Live, including
# the class_name keys themselves (Simpler is believed "OriginalSimpler").
#
# Property kinds:
#   "bool"     LOM bool property, exposed as true/false
#   "int"      LOM int property, raw integer
#   "enum"     LOM int property with fixed labels (index = LOM value)
#   "indexed"  LOM <name>_index property paired with a <name>_list
#              StringVector — labels come from Live at runtime, never
#              hard-coded (Drift's lists ship with Live, not with us)
#
# methods: name -> (gate_property_or_None, required_arg_names). A gate is a
# read-only bool class property that must be true for the call to be allowed.
_DRIFT_INDEXED = (
    "voice_mode",
    "voice_count",
    "mod_matrix_pitch_source_1",
    "mod_matrix_pitch_source_2",
    "mod_matrix_filter_source_1",
    "mod_matrix_filter_source_2",
    "mod_matrix_shape_source",
    "mod_matrix_lfo_source",
    "mod_matrix_source_1",
    "mod_matrix_source_2",
    "mod_matrix_source_3",
    "mod_matrix_target_1",
    "mod_matrix_target_2",
    "mod_matrix_target_3",
)

_CLASS_PROPS: dict[str, dict[str, Any]] = {
    "OriginalSimpler": {
        "writable": {
            "playback_mode": ("enum", ("Classic", "One-Shot", "Slicing")),
            "slicing_playback_mode": ("enum", ("Mono", "Poly", "Thru")),
            "retrigger": ("bool", None),
            "pad_slicing": ("bool", None),
            "voices": ("int", None),
        },
        "read_only": {
            "multi_sample_mode": ("bool", None),
            "can_warp_as": ("bool", None),
            "can_warp_double": ("bool", None),
            "can_warp_half": ("bool", None),
        },
        "methods": {
            "reverse": (None, ()),
            "crop": (None, ()),
            "warp_as": ("can_warp_as", ("beats",)),
            "warp_double": ("can_warp_double", ()),
            "warp_half": ("can_warp_half", ()),
            "guess_playback_length": (None, ()),
        },
    },
    "Eq8": {
        "writable": {
            "global_mode": ("enum", ("Stereo", "L/R", "M/S")),
            "edit_mode": ("bool", None),
            "oversample": ("bool", None),
        },
    },
    "Drift": {
        "writable": {
            **{name: ("indexed", None) for name in _DRIFT_INDEXED},
            "pitch_bend_range": ("int", None),
        },
    },
}


def _indexed_items(device: Any, name: str) -> list[str]:
    return [str(item) for item in getattr(device, f"{name}_list")]


def _serialize_class_properties(device: Any) -> dict[str, Any] | None:
    table = _CLASS_PROPS.get(device.class_name)
    if table is None:
        return None
    block: dict[str, Any] = {}
    sections = list(table.get("writable", {}).items()) + list(table.get("read_only", {}).items())
    for name, (kind, labels) in sections:
        try:
            if kind == "bool":
                block[name] = bool(getattr(device, name))
            elif kind == "int":
                block[name] = int(getattr(device, name))
            elif kind == "enum":
                value = int(getattr(device, name))
                label = labels[value] if 0 <= value < len(labels) else value
                block[name] = {"value": label, "items": list(labels)}
            elif kind == "indexed":
                items = _indexed_items(device, name)
                value = int(getattr(device, f"{name}_index"))
                label = items[value] if 0 <= value < len(items) else value
                block[name] = {"value": label, "items": items}
        except Exception:
            # A property the running Live doesn't have (older version, table
            # drift): skip it rather than fail the whole read.
            continue
    return block or None


def _available_class_methods(device: Any) -> list[str]:
    table = _CLASS_PROPS.get(device.class_name)
    if table is None or "methods" not in table:
        return []
    available = []
    for name, (gate, _args) in table["methods"].items():
        if gate is None or bool(getattr(device, gate, False)):
            available.append(name)
    return available


def _resolve_class_prop_write(device: Any, table: dict, name: str, value: Any):
    """(lom_attribute, int_or_bool_to_write, echo_value) — validation only."""
    kind, labels = table["writable"][name]
    if kind == "bool":
        if not isinstance(value, bool) and value not in (0, 1):
            raise LiveAPIError(f"'{name}' takes true or false, got {value!r}")
        return name, bool(value), bool(value)
    if kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise LiveAPIError(f"'{name}' takes an integer, got {value!r}") from None
        return name, number, number
    items = list(labels) if kind == "enum" else _indexed_items(device, name)
    attribute = name if kind == "enum" else f"{name}_index"
    if isinstance(value, bool):
        raise LiveAPIError(f"'{name}' takes one of {items} or an index, got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        index = int(value)
        if not 0 <= index < len(items):
            raise LiveAPIError(f"'{name}' index {index} out of range — choices: {items}")
        return attribute, index, items[index]
    match = next((i for i, s in enumerate(items) if s.lower() == str(value).lower()), None)
    if match is None:
        raise LiveAPIError(f"'{name}' has no choice {value!r}. Available: {items}")
    return attribute, match, items[match]


def _serialize_parameter(index: int, param: Any) -> dict[str, Any]:
    # Absent = default: is_quantized false, is_enabled true, automation_state
    # "none". The constant tail was ~40% of an EQ Eight dump.
    info = {
        "index": index,
        "name": param.name,
        "value": normalize_parameter(param),
        "display_value": str(param),
    }
    if param.is_quantized:
        info["is_quantized"] = True
    # False when a rack macro or live.remote~ owns the parameter — writes
    # would be refused, so surface it up front.
    if not getattr(param, "is_enabled", True):
        info["is_enabled"] = False
    automation = _AUTOMATION_STATES.get(getattr(param, "automation_state", 0), "none")
    if automation != "none":
        info["automation_state"] = automation
    if param.is_quantized:
        # Human-readable choices ("Lowpass", "24 dB", ...) — quantized
        # parameters only (LOM); without these the model must guess which
        # normalized number means which mode.
        try:
            info["value_items"] = [str(v) for v in param.value_items]
        except Exception:
            pass
    return info


@REGISTRY.register(
    "get_devices",
    params=[
        ParamSchema("track_index", ParamType.INT, required=False, min_value=0),
        TRACK_TYPE_PARAM,
        ParamSchema(
            "device_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Omit for the device list; set for one device's full parameters",
        ),
    ],
    category="devices",
    read_only=True,
    description=(
        "Devices on a regular, return, or the master track (track_type). "
        "Without device_index: summaries. With it: every parameter (values "
        "normalized 0-1) plus display values, enum choices (value_items), "
        "editability (is_enabled), and automation state. Known native devices "
        "(Simpler, EQ Eight, Drift) also report class_properties — state that "
        "never appears in the parameter list (playback modes, M/S switch, mod "
        "matrix) — and class_methods, both usable via set_device_parameters."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "devices": {"type": "array"},
            "device": {"type": "object"},
        },
    },
)
def get_devices(
    ctx,
    track_index: int | None = None,
    track_type: str = "track",
    device_index: int | None = None,
) -> dict[str, Any]:
    track = resolve_track(ctx.song, track_type, track_index)

    if device_index is None:
        return {
            "devices": [
                {
                    "index": i,
                    "name": device.name,
                    "class_name": device.class_name,
                    "is_active": device.is_active,
                    "parameter_count": len(list(device.parameters)),
                }
                for i, device in enumerate(track.devices)
            ]
        }

    device = get_device(track, device_index)
    info = {
        "index": device_index,
        "name": device.name,
        "class_name": device.class_name,
        "is_active": device.is_active,
        "parameters": [_serialize_parameter(i, p) for i, p in enumerate(device.parameters)],
    }
    class_properties = _serialize_class_properties(device)
    if class_properties:
        info["class_properties"] = class_properties
    methods = _available_class_methods(device)
    if methods:
        info["class_methods"] = methods
    return {"device": info}


@REGISTRY.register(
    "set_device_parameters",
    params=[
        ParamSchema("track_index", ParamType.INT, required=False, min_value=0),
        TRACK_TYPE_PARAM,
        ParamSchema("device_index", ParamType.INT, min_value=0),
        ParamSchema(
            "parameters",
            ParamType.OBJECT_LIST,
            required=False,
            description="Batch: [{parameter: name-or-index, value: 0-1 or class-property value}]",
            item_schema={
                "type": "object",
                "properties": {
                    "parameter": {
                        "description": (
                            "Parameter name (case-insensitive), integer index, or a "
                            "class_properties name from get_devices"
                        ),
                    },
                    "value": {
                        "description": (
                            "0-1 for regular parameters; for class properties: a "
                            "choice label, true/false, or integer"
                        ),
                    },
                },
                "required": ["parameter", "value"],
            },
        ),
        ParamSchema(
            "invoke",
            ParamType.OBJECT_LIST,
            required=False,
            description=(
                "Class methods to call after the writes, e.g. Simpler's "
                "[{method: 'reverse'}] or [{method: 'warp_as', beats: 4}] — "
                "see class_methods in get_devices"
            ),
            item_schema={
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "beats": {"type": "integer", "minimum": 1},
                },
                "required": ["method"],
            },
        ),
        ParamSchema("enabled", ParamType.BOOL, required=False),
        ParamSchema(
            "re_enable_automation",
            ParamType.BOOL,
            required=False,
            description="After setting values, restore any automation these writes overrode",
        ),
    ],
    category="devices",
    description=(
        "Set device parameters in one batch (values normalized 0-1) and/or "
        "enable/bypass the device: `enabled` drives its 'Device On' parameter "
        "(is_active is read-only and also reflects any enclosing rack). Works "
        "on regular, return, and the master track (track_type). Known native "
        "devices also take class_properties names (playback_mode, global_mode, "
        "voice_mode, ...) with label/bool/int values, and class method calls "
        "via `invoke` (reverse, crop, warp_as...)."
    ),
)
def set_device_parameters(
    ctx,
    track_index: int | None = None,
    track_type: str = "track",
    device_index: int = 0,
    parameters: list[dict[str, Any]] | None = None,
    invoke: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
    re_enable_automation: bool | None = None,
) -> dict[str, Any]:
    track = resolve_track(ctx.song, track_type, track_index)
    device = get_device(track, device_index)
    class_table = _CLASS_PROPS.get(device.class_name)

    # Resolve and validate EVERY selector and value before the first write, so
    # a bad later entry cannot leave the batch half-applied.
    resolved: list[tuple[Any, float]] = []
    class_writes: list[tuple[str, str, Any, Any]] = []  # (name, attribute, raw, echo)
    for item in parameters or []:
        if "parameter" not in item or "value" not in item:
            raise LiveAPIError("Each entry needs 'parameter' (name or index) and 'value'")
        selector = item["parameter"]
        # Class-property names win on exact (case-insensitive) match; they are
        # snake_case LOM names, so they can't collide with Live's display-cased
        # parameter names in practice.
        if (
            class_table is not None
            and isinstance(selector, str)
            and selector.lower() in class_table.get("writable", {})
        ):
            name = selector.lower()
            attribute, raw, echo = _resolve_class_prop_write(
                device, class_table, name, item["value"]
            )
            class_writes.append((name, attribute, raw, echo))
            continue
        param = resolve_device_parameter(device, selector)
        try:
            value = float(item["value"])
        except (TypeError, ValueError):
            raise LiveAPIError(
                f"Value for '{param.name}' is not a number: {item['value']!r}"
            ) from None
        if not 0.0 <= value <= 1.0:
            raise LiveAPIError(f"Value for '{param.name}' must be 0-1, got {value}")
        if not getattr(param, "is_enabled", True):
            raise LiveAPIError(
                f"Parameter '{param.name}' is not currently editable "
                f"(controlled by a rack macro or live.remote~)"
            )
        resolved.append((param, value))

    # Validate method invocations up front too (existence, gates, args).
    invocations: list[tuple[str, tuple]] = []
    for item in invoke or []:
        method = str(item.get("method", ""))
        table_methods = (class_table or {}).get("methods", {})
        if method not in table_methods:
            known = sorted(table_methods) or ["none for this device class"]
            raise LiveAPIError(f"Unknown class method {method!r}. Available: {known}")
        gate, arg_names = table_methods[method]
        if gate is not None and not bool(getattr(device, gate, False)):
            raise LiveAPIError(f"'{method}' is not available right now ({gate} is false)")
        args = []
        for arg in arg_names:
            if arg not in item:
                raise LiveAPIError(
                    f"'{method}' needs '{arg}' (e.g. {{method: '{method}', {arg}: 4}})"
                )
            args.append(int(item[arg]))
        invocations.append((method, tuple(args)))

    device_on = None
    if enabled is not None:
        # Live's is_active is get/observe-only (and also reflects an enclosing
        # Rack's switch); the writable control is the Device On parameter.
        device_on = next((p for p in device.parameters if p.name.lower() == "device on"), None)
        if device_on is None:
            raise LiveAPIError(
                f"Device '{device.name}' has no 'Device On' parameter — cannot toggle enabled"
            )

    changed = []
    applied: list[str] = []
    _write = batch_writer(applied)
    for name, attribute, raw, echo in class_writes:
        _write(
            name,
            lambda a=attribute, r=raw: setattr(device, a, r),
            label=f"class property '{name}'",
        )
        changed.append({"name": name, "value": echo})
    for param, value in resolved:
        _write(
            param.name,
            lambda p=param, v=value: setattr(p, "value", denormalize_parameter(p, v)),
            label=f"parameter '{param.name}'",
        )
        changed.append(
            {"name": param.name, "value": normalize_parameter(param), "display_value": str(param)}
        )

    if device_on is not None:
        _write(
            "enabled",
            lambda: setattr(
                device_on, "value", denormalize_parameter(device_on, 1.0 if enabled else 0.0)
            ),
        )

    invoked = []
    for method, args in invocations:
        outcome: dict[str, Any] = {"method": method}

        def _call(m=method, a=args, out=outcome):
            value = getattr(device, m)(*a)
            if value is not None:
                out["result"] = value

        _write(method, _call, label=f"method '{method}'")
        invoked.append(outcome)

    if re_enable_automation:
        # Restore automation control for exactly the parameters this batch
        # wrote (a value write flips automation from active to overridden).
        for param, _value in resolved:
            param.re_enable_automation()

    result: dict[str, Any] = {"changed": changed, "is_active": device.is_active}
    if invoked:
        result["invoked"] = invoked
    if enabled is not None:
        result["enabled_requested"] = enabled
        result["device_on"] = {"name": device_on.name, "value": normalize_parameter(device_on)}
    return result


@REGISTRY.register(
    "insert_device",
    params=[
        ParamSchema("track_index", ParamType.INT, required=False, min_value=0),
        TRACK_TYPE_PARAM,
        ParamSchema(
            "device_name",
            ParamType.STRING,
            description="Exact native device name, e.g. 'Reverb', 'EQ Eight', 'Operator'",
        ),
        ParamSchema(
            "device_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Chain position to insert at; omit to append at the end",
        ),
    ],
    category="devices",
    description=(
        "Insert a NATIVE Ableton device by exact name at a chain position — on "
        "a regular, return, or the master track (track_type; e.g. a Limiter on "
        "master) — without touching the browser or the selected track "
        "(Live 12.3+). One instrument per chain (Live refuses a second). "
        "Plug-ins, Max devices, and presets still need browse + load_item."
    ),
)
def insert_device(
    ctx,
    track_index: int | None = None,
    track_type: str = "track",
    device_name: str = "",
    device_index: int | None = None,
) -> dict[str, Any]:
    track = resolve_track(ctx.song, track_type, track_index)
    before = len(list(track.devices))
    target = before if device_index is None else max(0, min(device_index, before))
    try:
        # Track.insert_device, Live 12.3+ (LOM). Always pass an explicit index
        # so the landing position is deterministic. VERIFY at checkpoint:
        # unknown-name behaviour (assumed to raise) and return value (assumed
        # None; we re-scan the chain).
        track.insert_device(device_name, target)
    except Exception as e:
        raise LiveAPIError(
            f"Live could not insert '{device_name}' ({e}). Only native Live devices "
            f"work here — check the exact name, or use browse + load_item."
        ) from e
    devices = list(track.devices)
    if len(devices) <= before:
        raise LiveAPIError(
            f"Live did not add '{device_name}' — only native Live devices are supported "
            f"here; use browse + load_item for plug-ins and presets."
        )
    idx = min(target, len(devices) - 1)
    device = devices[idx]
    return {
        "inserted": {"index": idx, "name": device.name, "class_name": device.class_name},
        "device_count": len(devices),
    }


@REGISTRY.register(
    "delete_device",
    params=[
        ParamSchema("track_index", ParamType.INT, required=False, min_value=0),
        TRACK_TYPE_PARAM,
        ParamSchema("device_index", ParamType.INT, min_value=0),
    ],
    category="devices",
    destructive=True,
    description=(
        "Remove a device from a regular, return, or the master track's chain "
        "(track_type). Destructive."
    ),
)
def delete_device(
    ctx,
    track_index: int | None = None,
    track_type: str = "track",
    device_index: int = 0,
) -> dict[str, Any]:
    track = resolve_track(ctx.song, track_type, track_index)
    device = get_device(track, device_index)
    name = device.name
    track.delete_device(device_index)
    return {"deleted": name, "device_count": len(list(track.devices))}
