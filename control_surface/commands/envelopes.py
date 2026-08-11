"""Clip automation envelopes — the feature the 2026-08-11 spike proved feasible.

Values cross the wire normalized 0-1 (like device parameters) and are
denormalized into the target parameter's native range. Envelope targets are
either a device parameter (device_index + parameter) or the track mixer
(mixer_parameter: volume/pan) — exactly one.
"""

from typing import Any

from ..errors import ValidationError
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_device, get_track, resolve_clip_ref
from ..utils.normalize import denormalize_parameter, normalize_parameter

# Shared XOR clip addressing (same objects as clips.py uses)
from .clips import _ARR_XOR, _SLOT_XOR

_TARGET_PARAMS = [
    ParamSchema(
        "device_index",
        ParamType.INT,
        required=False,
        min_value=0,
        description="Device target: index on the track (with 'parameter')",
    ),
    ParamSchema(
        "parameter",
        ParamType.ANY,
        required=False,
        description="Device target: parameter name (case-insensitive) or index",
    ),
    ParamSchema(
        "mixer_parameter",
        ParamType.STRING,
        required=False,
        enum_values=["volume", "pan"],
        description="Mixer target: the track's volume or pan — exactly one target kind",
    ),
]


def _resolve_target_parameter(
    track: Any,
    device_index: int | None,
    parameter: Any,
    mixer_parameter: str | None,
) -> Any:
    device_target = device_index is not None or parameter is not None
    if device_target == (mixer_parameter is not None):
        raise ValidationError(
            "Provide exactly one target: device_index+parameter, OR mixer_parameter"
        )

    if mixer_parameter is not None:
        mixer = track.mixer_device
        return mixer.volume if mixer_parameter == "volume" else mixer.panning

    if device_index is None or parameter is None:
        raise ValidationError("Device targets need BOTH device_index and parameter")

    device = get_device(track, device_index)
    params = list(device.parameters)
    if isinstance(parameter, int) or (isinstance(parameter, str) and str(parameter).isdigit()):
        idx = int(parameter)
        if not 0 <= idx < len(params):
            raise LiveAPIError(
                f"Parameter index {idx} out of range (device has {len(params)} parameters)"
            )
        return params[idx]
    match = next((p for p in params if p.name.lower() == str(parameter).lower()), None)
    if match is None:
        names = [p.name for p in params[:30]]
        raise LiveAPIError(f"No parameter named '{parameter}'. Available: {names}")
    return match


@REGISTRY.register(
    "set_clip_envelope",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        *_TARGET_PARAMS,
        ParamSchema(
            "points",
            ParamType.OBJECT_LIST,
            description="Automation points, values normalized 0-1: [{time (beats), value}]",
            item_schema={
                "type": "object",
                "properties": {
                    "time": {"type": "number", "minimum": 0},
                    "value": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["time", "value"],
            },
        ),
        ParamSchema(
            "clear_first",
            ParamType.BOOL,
            required=False,
            default=True,
            description="Wipe the parameter's existing envelope before writing (default true)",
        ),
    ],
    category="envelopes",
    description=(
        "Draw automation on a clip: filter sweeps, volume fades, wobbles — any "
        "device parameter or the track's volume/pan. Points are step values in "
        "beats (each holds until the next); values are normalized 0-1 like "
        "set_device_parameters. Works on session and arrangement clips."
    ),
)
def set_clip_envelope(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    device_index: int | None = None,
    parameter: Any = None,
    mixer_parameter: str | None = None,
    points: list[dict[str, Any]] = None,
    clear_first: bool = True,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(track, slot_index, arrangement_clip_index)
    param = _resolve_target_parameter(track, device_index, parameter, mixer_parameter)

    if not points:
        raise ValidationError("points must contain at least one {time, value}")
    for p in points:
        if "time" not in p or "value" not in p:
            raise ValidationError("Each point needs 'time' and 'value'")

    if clear_first:
        clip.clear_envelope(param)

    envelope = clip.automation_envelope(param)
    if envelope is None:
        envelope = clip.create_automation_envelope(param)
    if envelope is None:
        raise LiveAPIError(f"Live would not create an envelope for '{param.name}' on this clip")

    ordered = sorted(points, key=lambda p: float(p["time"]))
    written = 0
    for i, point in enumerate(ordered):
        start = float(point["time"])
        if i + 1 < len(ordered):
            length = max(float(ordered[i + 1]["time"]) - start, 0.01)
        else:
            length = max(clip.length - start, 0.01)
        native = denormalize_parameter(param, float(point["value"]))
        envelope.insert_step(start, length, native)
        written += 1

    return {
        "parameter": param.name,
        "points_written": written,
        "clip_length": clip.length,
    }


@REGISTRY.register(
    "get_clip_envelope",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        *_TARGET_PARAMS,
        ParamSchema(
            "samples",
            ParamType.INT,
            required=False,
            default=17,
            min_value=2,
            max_value=65,
            description="How many evenly spaced points to read across the clip",
        ),
    ],
    category="envelopes",
    read_only=True,
    description=(
        "Read a clip's automation for one parameter as evenly sampled points "
        "(normalized 0-1 plus native values). exists:false when the parameter "
        "has no envelope on this clip."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "exists": {"type": "boolean"},
            "parameter": {"type": "string"},
            "points": {"type": "array"},
        },
    },
)
def get_clip_envelope(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    device_index: int | None = None,
    parameter: Any = None,
    mixer_parameter: str | None = None,
    samples: int = 17,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(track, slot_index, arrangement_clip_index)
    param = _resolve_target_parameter(track, device_index, parameter, mixer_parameter)

    envelope = clip.automation_envelope(param)
    if envelope is None:
        return {"exists": False, "parameter": param.name, "points": []}

    step = clip.length / (samples - 1)
    points = []
    for i in range(samples):
        t = round(i * step, 4)
        # Confirmed on real Live: value_at_time is start-EXCLUSIVE at step
        # boundaries. Sample a hair after t so each point reports the value
        # in effect FROM that time, which is what a reader expects.
        probe = min(t + 0.001, clip.length)
        native = envelope.value_at_time(probe)
        points.append(
            {
                "time": t,
                "value": round(normalize_parameter(param, native), 4),
                "native_value": native,
            }
        )
    return {"exists": True, "parameter": param.name, "points": points}


@REGISTRY.register(
    "clear_clip_envelopes",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        *_TARGET_PARAMS[:2],
        ParamSchema(
            "mixer_parameter",
            ParamType.STRING,
            required=False,
            enum_values=["volume", "pan"],
            description="Mixer target; omit ALL targets to wipe every envelope on the clip",
        ),
    ],
    category="envelopes",
    destructive=True,
    description=(
        "Remove automation from a clip: one parameter's envelope (give a "
        "target), or ALL envelopes on the clip (give none). Destructive."
    ),
)
def clear_clip_envelopes(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    device_index: int | None = None,
    parameter: Any = None,
    mixer_parameter: str | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(track, slot_index, arrangement_clip_index)

    no_target = device_index is None and parameter is None and mixer_parameter is None
    if no_target:
        clip.clear_all_envelopes()
        return {"cleared": "all"}

    param = _resolve_target_parameter(track, device_index, parameter, mixer_parameter)
    clip.clear_envelope(param)
    return {"cleared": param.name}
