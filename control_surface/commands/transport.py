"""Transport: playback, tempo, time signature, loop, metronome."""

from typing import Any

from ..config import SEEK_EPSILON
from ..errors import batch_writer
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.pitch import SHARP_NAMES, root_name_to_pitch_class


def _transport_state(song: Any) -> dict[str, Any]:
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
def get_transport_state(ctx) -> dict[str, Any]:
    return _transport_state(ctx.song)


@REGISTRY.register(
    "transport_control",
    params=[
        ParamSchema(
            "action",
            ParamType.STRING,
            enum_values=["play", "stop", "continue"],
            description="play starts from Live's start marker (from `position` if given); continue resumes from where playback stopped",
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
    description=(
        "Start, stop, or continue playback, optionally jumping to a position "
        "(in beats). A position jump from a stopped transport takes two "
        "internal round-trips — invisible to the caller."
    ),
)
def transport_control(ctx, action: str, position: float | None = None) -> dict[str, Any]:
    song = ctx.song
    if position is not None and abs(song.current_song_time - position) > SEEK_EPSILON:
        # Verified on real Live 12.4 (see create_locator): a current_song_time
        # write does NOT take effect within the same scheduled task. Starting
        # playback in the task that issues the seek would play from the OLD
        # playhead — so seek first, act on the repeat call.
        if song.is_playing and action in ("play", "continue"):
            # Already playing: the seek IS the whole action — no dependent
            # write follows, so no second phase. Deliberately no re-call of
            # start_playing (restart semantics on a playing transport are
            # unverified).
            song.current_song_time = position
            state = _transport_state(song)
            state["requested_position"] = position
            state["note"] = "Seek issued while playing; it lands on the next timer tick."
            return state
        if action == "stop":
            song.stop_playing()
        song.current_song_time = position
        return {
            "phase": "seeking",
            "note": (
                "Playhead seek issued; call transport_control again with the "
                "same arguments to complete the action."
            ),
        }
    if action == "play":
        if position is not None:
            # CONFIRMED on real Live 12.4.3 (2.3 checkpoint): start_playing
            # starts from Live's INSERT/START MARKER, not the playhead — the
            # seeked position would be ignored. continue_playing resumes from
            # the playhead the two-phase seek just parked.
            song.continue_playing()
        else:
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
            "back_to_arranger",
            ParamType.BOOL,
            required=False,
            description="Set false to hand control back to the arrangement timeline after session clips played over it",
        ),
    ],
    category="transport",
    description=(
        "Set any of: tempo (BPM), time signature, loop region (beats), metronome, "
        "song key/scale (the tonal anchor for everything composed), "
        "back-to-arranger. Batch-friendly: pass several at once. Arrangement "
        "recording lives in its own tool (arrangement_record) because it is "
        "destructive."
    ),
)
def set_transport(
    ctx,
    tempo: float | None = None,
    signature_numerator: int | None = None,
    signature_denominator: int | None = None,
    loop_enabled: bool | None = None,
    loop_start: float | None = None,
    loop_length: float | None = None,
    metronome: bool | None = None,
    scale_root: str | None = None,
    scale_name: str | None = None,
    scale_mode: bool | None = None,
    back_to_arranger: bool | None = None,
) -> dict[str, Any]:
    song = ctx.song

    # Pure-Python validation first: a bad root name used to raise mid-batch,
    # after tempo/signature/loop had already been written.
    pitch_class = root_name_to_pitch_class(scale_root) if scale_root is not None else None

    # scale_name is the only field Live itself validates (silent-keep on
    # unknown names, surfaced by read-back) — write it FIRST so its failure
    # leaves every other field untouched.
    if scale_name is not None:
        song.scale_name = scale_name
        # VERIFY at checkpoint (invalid-scale step): assumed Live silently
        # keeps the old scale on unknown names; the read-back turns that into
        # a typed error instead of pretending.
        if song.scale_name != scale_name:
            raise LiveAPIError(
                f"Live rejected scale name '{scale_name}' (kept '{song.scale_name}'). "
                f"Use a name from Live's scale chooser exactly."
            )

    applied: list[str] = ["scale_name"] if scale_name is not None else []
    _write = batch_writer(applied)

    if pitch_class is not None:
        _write("scale_root", lambda: setattr(song, "root_note", pitch_class))
    if scale_mode is not None:
        _write("scale_mode", lambda: setattr(song, "scale_mode", scale_mode))
    if tempo is not None:
        _write("tempo", lambda: setattr(song, "tempo", tempo))
    if signature_numerator is not None:
        _write(
            "signature_numerator",
            lambda: setattr(song, "signature_numerator", signature_numerator),
        )
    if signature_denominator is not None:
        _write(
            "signature_denominator",
            lambda: setattr(song, "signature_denominator", signature_denominator),
        )
    if loop_enabled is not None:
        _write("loop_enabled", lambda: setattr(song, "loop", loop_enabled))
    if loop_start is not None:
        _write("loop_start", lambda: setattr(song, "loop_start", loop_start))
    if loop_length is not None:
        _write("loop_length", lambda: setattr(song, "loop_length", loop_length))
    if metronome is not None:
        _write("metronome", lambda: setattr(song, "metronome", metronome))
    if back_to_arranger is not None:
        _write("back_to_arranger", lambda: setattr(song, "back_to_arranger", back_to_arranger))
    return _transport_state(song)
