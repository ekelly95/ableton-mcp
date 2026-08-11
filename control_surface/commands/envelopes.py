"""Clip automation envelopes — the feature the 2026-08-11 spike proved feasible.

Values cross the wire normalized 0-1 (like device parameters) and are
denormalized into the target parameter's native range. Envelope targets are
either a device parameter (device_index + parameter) or the track mixer
(mixer_parameter: volume/pan) — exactly one.

SESSION CLIPS ONLY: Live's own API docstring for Clip.automation_envelope says
"Returns None for Arrangement clips" — arrangement clips carry only modulation,
and their absolute automation lives on the track's automation lanes, which the
Python API does not expose. Unwarped audio clips are rejected too: their loop
bounds are in seconds and clip.length is undefined (LOM), so beat-based
envelope times would be silently wrong.
"""

from typing import Any

from ..errors import PartialApplyError, ValidationError
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_device, get_track, resolve_clip_ref
from ..utils.normalize import denormalize_parameter, normalize_parameter

# Session-slot addressing shared with clips.py; the arrangement counterpart is
# deliberately NOT shared — envelope tools reject it (see module docstring) but
# keep the parameter so a caller reaching for it gets a typed explanation
# instead of an "unexpected property" schema error.
from .clips import _SLOT_XOR

_ARR_REJECTED = ParamSchema(
    "arrangement_clip_index",
    ParamType.INT,
    required=False,
    min_value=0,
    description=(
        "NOT SUPPORTED: arrangement clips have no clip envelopes (Live's API "
        "returns None for them) — address a session clip via slot_index"
    ),
)

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


def _reject_arrangement_clip(arrangement_clip_index: int | None) -> None:
    if arrangement_clip_index is not None:
        raise ValidationError(
            "Arrangement clips have no clip envelopes — Live's API returns None for "
            "them, and their automation lives on track automation lanes (not exposed). "
            "Address a session clip via slot_index.",
            param="arrangement_clip_index",
        )


def _reject_unwarped_audio(clip: Any) -> None:
    # `warping` exists on audio clips only (LOM) — guard with is_audio_clip.
    if clip.is_audio_clip and not clip.warping:
        raise ValidationError(
            "Unwarped audio clip: its loop bounds are in seconds and clip.length is "
            "undefined, so beat-based envelope times would be wrong. Warp the clip "
            "first (set_clip warping=true).",
            param="slot_index",
        )


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


def _get_or_create_envelope(clip: Any, param: Any) -> Any:
    envelope = clip.automation_envelope(param)
    if envelope is None:
        envelope = clip.create_automation_envelope(param)
    return envelope


@REGISTRY.register(
    "set_clip_envelope",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_REJECTED,
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
        "Draw automation on a SESSION clip (MIDI or warped audio): filter "
        "sweeps, volume fades, wobbles — any device parameter or the track's "
        "volume/pan. Points are step values in beats (each holds until the "
        "next); values are normalized 0-1 like set_device_parameters. "
        "Arrangement clips cannot hold clip automation (Live API limit)."
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
    _reject_arrangement_clip(arrangement_clip_index)
    clip = resolve_clip_ref(track, slot_index, None)
    _reject_unwarped_audio(clip)
    param = _resolve_target_parameter(track, device_index, parameter, mixer_parameter)

    # Validate EVERY point before touching the clip: shape, numeric coercion,
    # value range, and time within the clip. (Previously a point at time=1000
    # on a 4-beat clip was silently written as a 0.01-beat step and counted as
    # a success.)
    if not points:
        raise ValidationError("points must contain at least one {time, value}")
    parsed: list[tuple[float, float]] = []
    for i, p in enumerate(points):
        if "time" not in p or "value" not in p:
            raise ValidationError("Each point needs 'time' and 'value'")
        try:
            t = float(p["time"])
            v = float(p["value"])
        except (TypeError, ValueError):
            raise ValidationError(f"Point {i}: time and value must be numbers") from None
        if not 0.0 <= v <= 1.0:
            raise ValidationError(f"Point {i}: value {v} is outside 0-1", param="points")
        if t > clip.length:
            raise ValidationError(
                f"Point {i}: time {t} is beyond the clip's length ({clip.length} beats)",
                param="points",
            )
        parsed.append((t, v))

    # Get-or-create BEFORE the destructive clear: if Live won't produce an
    # envelope for this parameter, nothing has been destroyed yet.
    envelope = _get_or_create_envelope(clip, param)
    if envelope is None:
        raise LiveAPIError(f"Live would not create an envelope for '{param.name}' on this clip")

    if clear_first:
        clip.clear_envelope(param)
        # Whether clearing invalidates a previously held envelope object is
        # unprobed on real Live — re-fetching makes the question irrelevant.
        envelope = _get_or_create_envelope(clip, param)
        if envelope is None:
            raise LiveAPIError(
                f"Envelope for '{param.name}' could not be re-created after clearing — "
                "the old envelope is gone; re-run with clear_first=false"
            )

    ordered = sorted(parsed)
    written = 0
    for i, (start, value) in enumerate(ordered):
        if i + 1 < len(ordered):
            length = max(ordered[i + 1][0] - start, 0.01)
        else:
            length = max(clip.length - start, 0.01)
        native = denormalize_parameter(param, value)
        try:
            # Event-level APIs (create_event / events_in_range, incl. curve
            # coefficients) exist in Live 12.4 but are intentionally unused —
            # v1 writes steps; see docs/architecture.md.
            envelope.insert_step(start, length, native)
        except Exception as e:
            raise PartialApplyError(
                f"point {i} (time {start})", str(e), [f"{written} earlier points"]
            ) from e
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
        _ARR_REJECTED,
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
        "Read a SESSION clip's automation for one parameter as evenly sampled "
        "points (normalized 0-1 plus native values). exists:false when the "
        "parameter has no envelope on this clip."
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
    _reject_arrangement_clip(arrangement_clip_index)
    clip = resolve_clip_ref(track, slot_index, None)
    # The sampled read divides clip.length, which is undefined for unwarped
    # audio — reject those here too.
    _reject_unwarped_audio(clip)
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
        _ARR_REJECTED,
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
        "Remove automation from a SESSION clip: one parameter's envelope (give "
        "a target), or ALL envelopes on the clip (give none). Destructive."
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
    _reject_arrangement_clip(arrangement_clip_index)
    clip = resolve_clip_ref(track, slot_index, None)
    # No unwarped guard: clearing does no time arithmetic, so cleaning up an
    # unwarped clip's envelopes is deliberately allowed.

    no_target = device_index is None and parameter is None and mixer_parameter is None
    if no_target:
        clip.clear_all_envelopes()
        return {"cleared": "all"}

    param = _resolve_target_parameter(track, device_index, parameter, mixer_parameter)
    clip.clear_envelope(param)
    return {"cleared": param.name}
