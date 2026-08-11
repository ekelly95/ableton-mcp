"""Transport: playback, tempo, time signature, loop, metronome."""

from typing import Any, Dict, Optional

from ..registry import REGISTRY, ParamSchema, ParamType


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
    ],
    category="transport",
    description="Set any of: tempo (BPM), time signature, loop region (beats), metronome. Batch-friendly: pass several at once.",
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
    return _transport_state(song)
