"""Devices: inspect, batch parameter setting (normalized 0-1), delete."""

from typing import Any, Dict, List, Optional

from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_device, get_track
from ..utils.normalize import denormalize_parameter, normalize_parameter


def _serialize_parameter(index: int, param: Any) -> Dict[str, Any]:
    return {
        "index": index,
        "name": param.name,
        "value": normalize_parameter(param),
        "display_value": str(param),
        "is_quantized": param.is_quantized,
    }


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
        "parameter (values normalized 0-1) plus human-readable display values."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "devices": {"type": "array"},
            "device": {"type": "object"},
        },
    },
)
def get_devices(ctx, track_index: int, device_index: Optional[int] = None) -> Dict[str, Any]:
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
            "parameters": [
                _serialize_parameter(i, p) for i, p in enumerate(device.parameters)
            ],
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
    ],
    category="devices",
    description=(
        "Set device parameters in one batch (values normalized 0-1) and/or "
        "enable/bypass the device. Parameter 0 is usually 'Device On'."
    ),
)
def set_device_parameters(
    ctx,
    track_index: int,
    device_index: int,
    parameters: Optional[List[Dict[str, Any]]] = None,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    track = get_track(ctx.song, track_index)
    device = get_device(track, device_index)
    params = list(device.parameters)
    by_name = {p.name.lower(): p for p in params}

    changed = []
    for item in parameters or []:
        if "parameter" not in item or "value" not in item:
            raise LiveAPIError("Each entry needs 'parameter' (name or index) and 'value'")
        selector = item["parameter"]
        if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
            idx = int(selector)
            if not 0 <= idx < len(params):
                raise LiveAPIError(
                    f"Parameter index {idx} out of range (device has {len(params)} parameters)"
                )
            param = params[idx]
        else:
            param = by_name.get(str(selector).lower())
            if param is None:
                names = [p.name for p in params[:30]]
                raise LiveAPIError(
                    f"No parameter named '{selector}'. Available: {names}"
                )
        param.value = denormalize_parameter(param, float(item["value"]))
        changed.append(
            {"name": param.name, "value": normalize_parameter(param), "display_value": str(param)}
        )

    if enabled is not None:
        device.is_active = enabled

    return {"changed": changed, "is_active": device.is_active}


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
def delete_device(ctx, track_index: int, device_index: int) -> Dict[str, Any]:
    track = get_track(ctx.song, track_index)
    device = get_device(track, device_index)
    name = device.name
    track.delete_device(device_index)
    return {"deleted": name, "device_count": len(list(track.devices))}
