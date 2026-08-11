"""Transport: playback, tempo, time signature, loop, metronome."""

from typing import Any, Dict, Optional

from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.pitch import SHARP_NAMES, root_name_to_pitch_class


def _transport_state(song: Any) -> Dict[str, Any]:
    return {
        "is_playing": song.is_playing,
        "tempo": song.tempo,
        "signature_numerator": song.signature_numerator,
        "signature_denominator": song.signature_denominator,
        "metronome": song.metronome,
        "loop": {
            "enabled": song.loop,
            "start": song.loop_start,
            "length": song.loop_length,
        },
        "current_song_time": song.current_song_time,
        "scale": {
            "root": SHARP_NAMES[song.root_note],
            "root_note": song.root_note,
            "name": song.scale_name,
            "scale_mode": song.scale_mode,
            "intervals": list(song.scale_intervals),
        },
        "record_mode": song.record_mode,
        "back_to_arranger": song.back_to_arranger,
    }


@REGISTRY.register(
    "get_transport_state",
    params=[],
    category="transport",
    read_only=True,
    description="Get playback state: playing, tempo, time signature, metronome, loop, position.",
    output_schema={
        "type": "object",
        "properties": {
            "is_playing": {"type": "boolean"},
            "tempo": {"type": "number"},
            "signature_numerator": {"type": "integer"},
            "signature_denominator": {"type": "integer"},
            "metronome": {"type": "boolean"},
            "loop": {"type": "object"},
            "current_song_time": {"type": "number", "description": "In beats"},
        },
    },
)
def get_transport_state(ctx) -> Dict[str, Any]:
    return _transport_state(ctx.song)


@REGISTRY.register(
    "transport_control",
    params=[
        ParamSchema(
            "action",
            ParamType.STRING,
            enum_values=["play", "stop", "continue"],
            description="play starts from the current position marker; continue resumes from where playback stopped",
        ),
        ParamSchema(
            "position",
            ParamType.FLOAT,
            required=False,
            min_value=0,
            description="Optional song position in beats to jump to first",
        ),
    ],
    category="transport",
    description="Start, stop, or continue playback, optionally jumping to a position (in beats).",
)
def transport_control(ctx, action: str, position: Optional[float] = None) -> Dict[str, Any]:
    song = ctx.song
    if position is not None:
        song.current_song_time = position
    if action == "play":
        song.start_playing()
    elif action == "continue":
        song.continue_playing()
    else:
        song.stop_playing()
    return _transport_state(song)


@REGISTRY.register(
    "set_transport",
    params=[
        ParamSchema("tempo", ParamType.FLOAT, required=False, min_value=20, max_value=999),
        ParamSchema(
            "signature_numerator", ParamType.INT, required=False, min_value=1, max_value=99
        ),
        ParamSchema(
            "signature_denominator",
            ParamType.INT,
            required=False,
            enum_values=[1, 2, 4, 8, 16],
        ),
        ParamSchema("loop_enabled", ParamType.BOOL, required=False),
        ParamSchema("loop_start", ParamType.FLOAT, required=False, min_value=0),
        ParamSchema("loop_length", ParamType.FLOAT, required=False, min_value=0.25),
        ParamSchema("metronome", ParamType.BOOL, required=False),
        ParamSchema(
            "scale_root",
            ParamType.ANY,
            required=False,
            description="Key root as a note name ('D', 'F#', 'Bb') or 0-11 (0=C)",
        ),
        ParamSchema(
            "scale_name",
            ParamType.STRING,
            required=False,
            description=(
                "Live scale name, e.g. Major, Minor, Dorian, Mixolydian, Lydian, "
                "Phrygian, Locrian, Harmonic Minor, Melodic Minor, Major Pentatonic, "
                "Minor Pentatonic, Major Blues, Minor Blues (must match Live's list exactly)"
            ),
        ),
        ParamSchema(
            "scale_mode",
            ParamType.BOOL,
            required=False,
            description="Highlight the scale in Live's editors and snap MIDI tools to it",
        ),
        ParamSchema(
            "record_mode",
            ParamType.BOOL,
            required=False,
            description="Arrangement record button. WARNING: recording while playing overwrites the arrangement",
        ),
        ParamSchema(
            "back_to_arranger",
            ParamType.BOOL,
            required=False,
            description="Set false to hand control back to the arrangement timeline after session clips played over it",
        ),
    ],
    category="transport",
    description=(
        "Set any of: tempo (BPM), time signature, loop region (beats), metronome, "
        "song key/scale (the tonal anchor for everything composed), arrangement "
        "record mode, back-to-arranger. Batch-friendly: pass several at once."
    ),
)
def set_transport(
    ctx,
    tempo=None,
    signature_numerator=None,
    signature_denominator=None,
    loop_enabled=None,
    loop_start=None,
    loop_length=None,
    metronome=None,
    scale_root=None,
    scale_name=None,
    scale_mode=None,
    record_mode=None,
    back_to_arranger=None,
) -> Dict[str, Any]:
    song = ctx.song
    if tempo is not None:
        song.tempo = tempo
    if signature_numerator is not None:
        song.signature_numerator = signature_numerator
    if signature_denominator is not None:
        song.signature_denominator = signature_denominator
    if loop_enabled is not None:
        song.loop = loop_enabled
    if loop_start is not None:
        song.loop_start = loop_start
    if loop_length is not None:
        song.loop_length = loop_length
    if metronome is not None:
        song.metronome = metronome
    if scale_root is not None:
        song.root_note = root_name_to_pitch_class(scale_root)
    if scale_name is not None:
        song.scale_name = scale_name
        # VERIFY at checkpoint: invalid names may silently no-op in Live —
        # read back and surface the failure instead of pretending.
        if song.scale_name != scale_name:
            raise LiveAPIError(
                f"Live rejected scale name '{scale_name}' (kept '{song.scale_name}'). "
                f"Use a name from Live's scale chooser exactly."
            )
    if scale_mode is not None:
        song.scale_mode = scale_mode
    if record_mode is not None:
        song.record_mode = record_mode
    if back_to_arranger is not None:
        song.back_to_arranger = back_to_arranger
    return _transport_state(song)
