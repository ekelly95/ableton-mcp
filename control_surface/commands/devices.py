"""Devices: inspect, batch parameter setting (normalized 0-1), delete."""

from typing import Any

from ..errors import batch_writer
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_device, get_track, resolve_device_parameter
from ..utils.normalize import denormalize_parameter, normalize_parameter

# LOM DeviceParameter.automation_state values.
_AUTOMATION_STATES = {0: "none", 1: "active", 2: "overridden"}


def _serialize_parameter(index: int, param: Any) -> dict[str, Any]:
    info = {
        "index": index,
        "name": param.name,
        "value": normalize_parameter(param),
        "display_value": str(param),
        "is_quantized": param.is_quantized,
        # False when a rack macro or live.remote~ owns the parameter — writes
        # would be refused, so surface it up front.
        "is_enabled": getattr(param, "is_enabled", True),
        "automation_state": _AUTOMATION_STATES.get(getattr(param, "automation_state", 0), "none"),
    }
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
        ParamSchema("track_index", ParamType.INT, min_value=0),
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
        "Devices on a track. Without device_index: summaries. With it: every "
        "parameter (values normalized 0-1) plus display values, enum choices "
        "(value_items), editability (is_enabled), and automation state."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "devices": {"type": "array"},
            "device": {"type": "object"},
        },
    },
)
def get_devices(ctx, track_index: int, device_index: int | None = None) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)

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
    return {
        "device": {
            "index": device_index,
            "name": device.name,
            "class_name": device.class_name,
            "is_active": device.is_active,
            "parameters": [_serialize_parameter(i, p) for i, p in enumerate(device.parameters)],
        }
    }


@REGISTRY.register(
    "set_device_parameters",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("device_index", ParamType.INT, min_value=0),
        ParamSchema(
            "parameters",
            ParamType.OBJECT_LIST,
            required=False,
            description="Batch: [{parameter: name-or-index, value: 0-1}]",
            item_schema={
                "type": "object",
                "properties": {
                    "parameter": {
                        "description": "Parameter name (case-insensitive) or integer index",
                    },
                    "value": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["parameter", "value"],
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
        "(is_active is read-only and also reflects any enclosing rack)."
    ),
)
def set_device_parameters(
    ctx,
    track_index: int,
    device_index: int,
    parameters: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
    re_enable_automation: bool | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    device = get_device(track, device_index)

    # Resolve and validate EVERY selector and value before the first write, so
    # a bad later entry cannot leave the batch half-applied.
    resolved: list[tuple[Any, float]] = []
    for item in parameters or []:
        if "parameter" not in item or "value" not in item:
            raise LiveAPIError("Each entry needs 'parameter' (name or index) and 'value'")
        param = resolve_device_parameter(device, item["parameter"])
        try:
            value = float(item["value"])
        except (TypeError, ValueError):
            raise LiveAPIError(
                f"Value for '{param.name}' is not a number: {item['value']!r}"
            ) from None
        if not getattr(param, "is_enabled", True):
            raise LiveAPIError(
                f"Parameter '{param.name}' is not currently editable "
                f"(controlled by a rack macro or live.remote~)"
            )
        resolved.append((param, value))

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

    if re_enable_automation:
        # Restore automation control for exactly the parameters this batch
        # wrote (a value write flips automation from active to overridden).
        for param, _value in resolved:
            param.re_enable_automation()

    result: dict[str, Any] = {"changed": changed, "is_active": device.is_active}
    if enabled is not None:
        result["enabled_requested"] = enabled
        result["device_on"] = {"name": device_on.name, "value": normalize_parameter(device_on)}
    return result


@REGISTRY.register(
    "insert_device",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
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
        "Insert a NATIVE Ableton device by exact name at a chain position, "
        "without touching the browser or the selected track (Live 12.3+). "
        "Plug-ins, Max devices, and presets still need browse + load_item."
    ),
)
def insert_device(
    ctx, track_index: int, device_name: str, device_index: int | None = None
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
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
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("device_index", ParamType.INT, min_value=0),
    ],
    category="devices",
    destructive=True,
    description="Remove a device from a track's chain. Destructive.",
)
def delete_device(ctx, track_index: int, device_index: int) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    device = get_device(track, device_index)
    name = device.name
    track.delete_device(device_index)
    return {"deleted": name, "device_count": len(list(track.devices))}
