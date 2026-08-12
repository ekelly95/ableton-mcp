"""Clips, scenes, launching, and MIDI notes via the modern note-ID API."""

from typing import Any

from ..config import MAX_COLOR_INDEX, MAX_NOTES_PER_READ
from ..errors import ValidationError, batch_writer
from ..registry import NOTE_OBJECT_SCHEMA, REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import (
    get_clip,
    get_clip_slot,
    get_scene,
    get_track,
    resolve_clip_ref,
)
from ..utils.pitch import midi_to_pitch_name, pitch_to_midi
from .tracks import serialize_clip_summary

# Shared by the five commands that address a clip in EITHER view.
_SLOT_XOR = ParamSchema(
    "slot_index",
    ParamType.INT,
    required=False,
    min_value=0,
    description="Session clip slot — give exactly one of slot_index / arrangement_clip_index",
)
_ARR_XOR = ParamSchema(
    "arrangement_clip_index",
    ParamType.INT,
    required=False,
    min_value=0,
    description="Timeline clip (positional, from get_arrangement) — exactly one of the two",
)
_TAKE_LANE = ParamSchema(
    "take_lane_index",
    ParamType.INT,
    required=False,
    min_value=0,
    description=(
        "Address a take-lane clip: arrangement_clip_index then counts within "
        "this lane (lanes and their clips come from get_arrangement)"
    ),
)

# Full-range note query bounds: all pitches, generous time span.
_ALL_PITCHES = (0, 128)
_MAX_TIME_SPAN = 1_000_000.0


def _serialize_note(note: Any) -> dict[str, Any]:
    # Fields at Live's defaults are omitted (absent = default) — on real clips
    # 4 of these fields are noise on nearly every note, and note payloads are
    # the bridge's dominant recurring token cost. pitch_name stays: what Claude
    # says must match what the user sees in Live's piano roll.
    serialized = {
        "note_id": note.note_id,
        "pitch": note.pitch,
        "pitch_name": midi_to_pitch_name(note.pitch),
        "start_time": note.start_time,
        "duration": note.duration,
        "velocity": note.velocity,
    }
    if note.mute:
        serialized["mute"] = note.mute
    if note.probability != 1.0:
        serialized["probability"] = note.probability
    if note.velocity_deviation != 0.0:
        serialized["velocity_deviation"] = note.velocity_deviation
    if note.release_velocity != 64.0:
        serialized["release_velocity"] = note.release_velocity
    return serialized


def _fetch_all_notes(clip: Any):
    return clip.get_notes_extended(_ALL_PITCHES[0], _ALL_PITCHES[1], 0.0, _MAX_TIME_SPAN)


@REGISTRY.register(
    "get_clips",
    params=[
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Limit to one track; omit for all tracks",
        ),
    ],
    category="clips",
    read_only=True,
    description="Clip slots per track: which have clips, names, lengths, play state. Scene names included.",
    output_schema={
        "type": "object",
        "properties": {
            "tracks": {"type": "array"},
            "scenes": {"type": "array"},
        },
    },
)
def get_clips(ctx, track_index: int | None = None) -> dict[str, Any]:
    song = ctx.song
    if track_index is not None:
        tracks = [(track_index, get_track(song, track_index))]
    else:
        tracks = list(enumerate(song.tracks))

    result = []
    for index, track in tracks:
        slots = [serialize_clip_summary(i, slot) for i, slot in enumerate(track.clip_slots)]
        result.append({"track_index": index, "track_name": track.name, "clip_slots": slots})

    scenes = [{"scene_index": i, "name": scene.name} for i, scene in enumerate(song.scenes)]
    return {"tracks": result, "scenes": scenes}


@REGISTRY.register(
    "create_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("slot_index", ParamType.INT, min_value=0),
        ParamSchema(
            "length_beats", ParamType.FLOAT, min_value=0.25, description="Clip length in beats"
        ),
    ],
    category="clips",
    description="Create an empty MIDI clip in a slot on a MIDI track.",
)
def create_clip(ctx, track_index: int, slot_index: int, length_beats: float) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    if not track.has_midi_input:
        raise LiveAPIError(f"Track {track_index} is not a MIDI track")
    slot = get_clip_slot(track, slot_index)
    if slot.has_clip:
        raise LiveAPIError(f"Slot {slot_index} already has a clip")
    slot.create_clip(length_beats)
    return {
        "track_index": track_index,
        "slot_index": slot_index,
        "length": slot.clip.length,
    }


@REGISTRY.register(
    "duplicate_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("slot_index", ParamType.INT, min_value=0),
    ],
    category="clips",
    description="Duplicate a clip into the next slot on the same track (fails if that slot is occupied).",
)
def duplicate_clip(ctx, track_index: int, slot_index: int) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    get_clip(track, slot_index)
    try:
        track.duplicate_clip_slot(slot_index)
    except RuntimeError as e:
        raise LiveAPIError(str(e)) from e
    return {"track_index": track_index, "source_slot": slot_index, "new_slot": slot_index + 1}


@REGISTRY.register(
    "delete_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("slot_index", ParamType.INT, min_value=0),
    ],
    category="clips",
    destructive=True,
    description="Delete the clip in a slot. Destructive.",
)
def delete_clip(ctx, track_index: int, slot_index: int) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    name = get_clip(track, slot_index).name
    get_clip_slot(track, slot_index).delete_clip()
    return {"deleted": name, "track_index": track_index, "slot_index": slot_index}


@REGISTRY.register(
    "set_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        _TAKE_LANE,
        ParamSchema("name", ParamType.STRING, required=False),
        ParamSchema(
            "color_index", ParamType.INT, required=False, min_value=0, max_value=MAX_COLOR_INDEX
        ),
        ParamSchema("looping", ParamType.BOOL, required=False),
        ParamSchema("loop_start", ParamType.FLOAT, required=False, min_value=0),
        ParamSchema("loop_end", ParamType.FLOAT, required=False, min_value=0),
        ParamSchema(
            "warping",
            ParamType.BOOL,
            required=False,
            description="Audio clips only: warp on/off (unwarped clips measure loop bounds in SECONDS, not beats)",
        ),
    ],
    category="clips",
    description="Batch setter for a clip's name, color, warp state, and loop settings (beats; seconds on unwarped audio). Addresses a session OR arrangement clip (exactly one of slot_index / arrangement_clip_index). Writes apply in order; a mid-batch Live error names what already landed.",
)
def set_clip(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    take_lane_index: int | None = None,
    name: str | None = None,
    color_index: int | None = None,
    looping: bool | None = None,
    loop_start: float | None = None,
    loop_end: float | None = None,
    warping: bool | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(
        track, slot_index, arrangement_clip_index, take_lane_index=take_lane_index
    )

    # All cross-field validation happens before the first write.
    if warping is not None and not clip.is_audio_clip:
        raise ValidationError("warping applies to audio clips only", param="warping")
    if loop_start is not None or loop_end is not None:
        eff_start = loop_start if loop_start is not None else clip.loop_start
        eff_end = loop_end if loop_end is not None else clip.loop_end
        if eff_start >= eff_end:
            raise ValidationError(
                f"loop_start must be < loop_end (effective {eff_start}..{eff_end})",
                param="loop_start" if loop_start is not None else "loop_end",
            )

    applied: list[str] = []
    _write = batch_writer(applied)

    if name is not None:
        _write("name", lambda: setattr(clip, "name", name))
    if color_index is not None:
        _write("color_index", lambda: setattr(clip, "color_index", color_index))
    if warping is not None:
        _write("warping", lambda: setattr(clip, "warping", warping))
    if looping is not None:
        _write("looping", lambda: setattr(clip, "looping", looping))
    if loop_start is not None or loop_end is not None:
        # Order the two writes so no intermediate state has start >= end:
        # if the new start fits under the CURRENT end, write start first;
        # otherwise the new end must clear the current start — write it first.
        start_first = loop_start is not None and loop_start < clip.loop_end
        if start_first:
            if loop_start is not None:
                _write("loop_start", lambda: setattr(clip, "loop_start", loop_start))
            if loop_end is not None:
                _write("loop_end", lambda: setattr(clip, "loop_end", loop_end))
        else:
            if loop_end is not None:
                _write("loop_end", lambda: setattr(clip, "loop_end", loop_end))
            if loop_start is not None:
                _write("loop_start", lambda: setattr(clip, "loop_start", loop_start))

    result = {
        "track_index": track_index,
        "slot_index": slot_index,
        "arrangement_clip_index": arrangement_clip_index,
        "name": clip.name,
        "looping": clip.looping,
        "loop_start": clip.loop_start,
        "loop_end": clip.loop_end,
    }
    if clip.is_audio_clip:
        result["warping"] = clip.warping
    return result


@REGISTRY.register(
    "launch_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema("slot_index", ParamType.INT, min_value=0),
    ],
    category="clips",
    description="Fire a clip (starts quantized to the launch quantization, like clicking its play button).",
)
def launch_clip(ctx, track_index: int, slot_index: int) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    get_clip(track, slot_index)
    get_clip_slot(track, slot_index).fire()
    return {"launched": True, "track_index": track_index, "slot_index": slot_index}


@REGISTRY.register(
    "stop_clips",
    params=[
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Stop one track's clips; omit to stop all session clips (transport keeps running)",
        ),
    ],
    category="clips",
    description="Stop session clips on one track or all tracks. Does not stop the transport.",
)
def stop_clips(ctx, track_index: int | None = None) -> dict[str, Any]:
    song = ctx.song
    if track_index is not None:
        get_track(song, track_index).stop_all_clips()
        return {"stopped": "track", "track_index": track_index}
    song.stop_all_clips()
    return {"stopped": "all"}


@REGISTRY.register(
    "launch_scene",
    params=[ParamSchema("scene_index", ParamType.INT, min_value=0)],
    category="clips",
    description="Fire a whole scene (row) of clips.",
)
def launch_scene(ctx, scene_index: int) -> dict[str, Any]:
    scene = get_scene(ctx.song, scene_index)
    scene.fire()
    return {"launched": True, "scene_index": scene_index}


@REGISTRY.register(
    "create_scene",
    params=[
        ParamSchema(
            "index", ParamType.INT, required=False, default=-1, description="-1 appends at the end"
        ),
    ],
    category="clips",
    description="Insert a new empty scene (row).",
)
def create_scene(ctx, index: int = -1) -> dict[str, Any]:
    song = ctx.song
    song.create_scene(index)
    scene_count = len(list(song.scenes))
    new_index = index if 0 <= index < scene_count else scene_count - 1
    return {"scene_index": new_index, "scene_count": scene_count}


@REGISTRY.register(
    "delete_scene",
    params=[ParamSchema("scene_index", ParamType.INT, min_value=0)],
    category="clips",
    destructive=True,
    description="Delete a scene and every clip in it. Destructive.",
)
def delete_scene(ctx, scene_index: int) -> dict[str, Any]:
    song = ctx.song
    get_scene(song, scene_index)
    song.delete_scene(scene_index)
    return {"deleted_scene": scene_index, "scene_count": len(list(song.scenes))}


# --- MIDI notes (modern note-ID API, Live 11.1+) ---


@REGISTRY.register(
    "get_notes",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        _TAKE_LANE,
        ParamSchema(
            "from_pitch", ParamType.INT, required=False, default=0, min_value=0, max_value=127
        ),
        ParamSchema("pitch_span", ParamType.INT, required=False, default=128, min_value=1),
        ParamSchema("from_time", ParamType.FLOAT, required=False, default=0.0, min_value=0),
        ParamSchema(
            "time_span",
            ParamType.FLOAT,
            required=False,
            min_value=0,
            description="Defaults to the whole clip",
        ),
        ParamSchema(
            "format",
            ParamType.STRING,
            required=False,
            default="json",
            enum_values=["json", "compact"],
            description=(
                "'compact' returns one notation string instead of note objects — "
                "far fewer tokens, but no note_ids; use 'json' before surgical edits"
            ),
        ),
    ],
    category="notes",
    read_only=True,
    description=(
        "Read MIDI notes from a clip. Every note has a stable note_id usable with "
        "update_notes/remove_notes. Times are in beats. Fields at Live defaults "
        "are omitted (absent = default)."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "notes": {"type": "array"},
            "note_count": {"type": "integer"},
            "truncated": {"type": "boolean"},
            "clip_length": {"type": "number", "description": "In beats"},
        },
    },
)
def get_notes(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    take_lane_index: int | None = None,
    from_pitch: int = 0,
    pitch_span: int = 128,
    from_time: float = 0.0,
    time_span: float | None = None,
    format: str = "json",  # rendering happens in the MCP server; accepted here
    # so a drifted server forwarding it doesn't crash the command.
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(
        track,
        slot_index,
        arrangement_clip_index,
        require_midi=True,
        take_lane_index=take_lane_index,
    )
    span = time_span if time_span is not None else max(clip.length - from_time, 0.0)
    notes = clip.get_notes_extended(from_pitch, pitch_span, from_time, span)

    truncated = len(notes) > MAX_NOTES_PER_READ
    serialized = [_serialize_note(n) for n in list(notes)[:MAX_NOTES_PER_READ]]
    return {
        "notes": serialized,
        "note_count": len(notes),
        "truncated": truncated,
        "clip_length": clip.length,
    }


@REGISTRY.register(
    "add_notes",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        _TAKE_LANE,
        ParamSchema(
            "notes",
            ParamType.NOTE_LIST,
            required=False,
            description=(
                "Notes to add; times/durations in beats; pitch as MIDI number or "
                "name like 'C3' (ABLETON convention: C3=60). Existing notes are kept."
            ),
        ),
        ParamSchema(
            "notation",
            ParamType.STRING,
            required=False,
            description=(
                "Compact alternative to notes — STRONGLY preferred (10-100x fewer "
                "tokens). Stateful prefixes v100 (velocity, v90-110=range) n/16 "
                "(duration, d=dotted t=triplet) p0.9 (probability); pitches C3 E3 "
                "(C3=60); place at bar|beat 1|1 (beat=quarter-note); repeat "
                "1|1x8@n/8; copy bars @2=1 @3-8=1-2; v0 deletes. Example: "
                "'v100 n/16 C1 1|1 D1 1|3 F#1 1|1x16@n/16 @2-4=1'"
            ),
        ),
        ParamSchema(
            "transforms",
            ParamType.STRING,
            required=False,
            description=(
                "Transform statements applied to the incoming notes before they "
                "are written (';'-separated, see transform_clip for the language), "
                "e.g. 'timing = swing(0.57); velocity = 90 + 30 * tri(1bar)'"
            ),
        ),
    ],
    category="notes",
    description=(
        "Add MIDI notes to a session or arrangement clip in one batch, as either "
        "a notes array or a notation string (exactly one of the two). Pitches "
        "accept names ('C3', 'F#4' — Ableton convention: C3=60). Supports "
        "per-note probability and velocity deviation (Live 11+)."
    ),
)
def add_notes(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    take_lane_index: int | None = None,
    notes: list[dict[str, Any]] | None = None,
    notation: str | None = None,  # expanded to notes by the MCP server; accepted
    transforms: str | None = None,  # applied by the MCP server; both accepted
    # here only so a drifted server forwarding them doesn't crash the command.
) -> dict[str, Any]:
    import Live  # inside Live's runtime only; tests install a mock module

    if not notes:
        raise ValidationError(
            "add_notes needs 'notes' (or 'notation', which the MCP server expands "
            "into notes before the command reaches Live)",
            param="notes",
        )
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(
        track,
        slot_index,
        arrangement_clip_index,
        require_midi=True,
        take_lane_index=take_lane_index,
    )

    specs = tuple(
        Live.Clip.MidiNoteSpecification(
            pitch=n["pitch"],
            start_time=n["start_time"],
            duration=n["duration"],
            velocity=n["velocity"],
            mute=n["mute"],
            probability=n["probability"],
            velocity_deviation=n["velocity_deviation"],
            release_velocity=n["release_velocity"],
        )
        for n in notes
    )
    clip.add_new_notes(specs)

    total = len(_fetch_all_notes(clip))
    return {"added": len(notes), "note_count": total}


@REGISTRY.register(
    "update_notes",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        _TAKE_LANE,
        ParamSchema(
            "modifications",
            ParamType.OBJECT_LIST,
            description="Per-note edits addressed by note_id from get_notes",
            # Derived from NOTE_OBJECT_SCHEMA (single source) — defaults are
            # stripped because an edit leaves unspecified fields untouched.
            item_schema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    **{
                        name: {k: v for k, v in prop.items() if k != "default"}
                        for name, prop in NOTE_OBJECT_SCHEMA["properties"].items()
                    },
                },
                "required": ["note_id"],
            },
        ),
    ],
    category="notes",
    description="Edit existing notes precisely by note_id (from get_notes) — change pitch, timing, velocity, probability, etc. without rewriting the clip.",
)
def update_notes(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    take_lane_index: int | None = None,
    modifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(
        track,
        slot_index,
        arrangement_clip_index,
        require_midi=True,
        take_lane_index=take_lane_index,
    )

    # Fetch-modify-apply: note objects can't be constructed by scripts, only
    # obtained from the clip, mutated, and handed back. Verified in real Live
    # 12.4: apply_note_modifications rejects a Python tuple of notes — it needs
    # the native vector get_notes_extended returned, so we mutate notes inside
    # that vector and pass the vector itself back.
    note_vector = _fetch_all_notes(clip)
    all_notes = {n.note_id: n for n in note_vector}

    editable = {
        # Names like "C3" are converted before the cast (Ableton convention C3=60)
        "pitch": lambda v: pitch_to_midi(v, param="modifications"),
        "start_time": float,
        "duration": float,
        "velocity": float,
        "mute": bool,
        "probability": float,
        "velocity_deviation": float,
        "release_velocity": float,
    }

    touched = []
    missing = []
    for mod in modifications:
        if "note_id" not in mod:
            raise LiveAPIError("Each modification needs a note_id (from get_notes)")
        note = all_notes.get(int(mod["note_id"]))
        if note is None:
            missing.append(int(mod["note_id"]))
            continue
        for field, cast in editable.items():
            if field in mod and mod[field] is not None:
                setattr(note, field, cast(mod[field]))
        touched.append(note)

    if missing:
        raise LiveAPIError(f"Unknown note_ids: {missing}. Fetch current ids with get_notes first.")

    if touched:
        clip.apply_note_modifications(note_vector)

    return {"updated": len(touched)}


@REGISTRY.register(
    "remove_notes",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        _SLOT_XOR,
        _ARR_XOR,
        _TAKE_LANE,
        ParamSchema(
            "note_ids",
            ParamType.INT_LIST,
            required=False,
            description="Remove exactly these notes (from get_notes)",
        ),
        ParamSchema("from_pitch", ParamType.INT, required=False, min_value=0, max_value=127),
        ParamSchema("pitch_span", ParamType.INT, required=False, min_value=1),
        ParamSchema("from_time", ParamType.FLOAT, required=False, min_value=0),
        ParamSchema("time_span", ParamType.FLOAT, required=False, min_value=0),
    ],
    category="notes",
    destructive=True,
    description=(
        "Remove notes either by note_ids OR by region (from_pitch/pitch_span/from_time/time_span; "
        "unspecified region fields default to everything). Destructive."
    ),
)
def remove_notes(
    ctx,
    track_index: int,
    slot_index: int | None = None,
    arrangement_clip_index: int | None = None,
    take_lane_index: int | None = None,
    note_ids: list[int] | None = None,
    from_pitch: int | None = None,
    pitch_span: int | None = None,
    from_time: float | None = None,
    time_span: float | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = resolve_clip_ref(
        track,
        slot_index,
        arrangement_clip_index,
        require_midi=True,
        take_lane_index=take_lane_index,
    )

    region_given = any(v is not None for v in (from_pitch, pitch_span, from_time, time_span))

    if note_ids:
        clip.remove_notes_by_id(tuple(note_ids))
    elif region_given:
        clip.remove_notes_extended(
            from_pitch if from_pitch is not None else _ALL_PITCHES[0],
            pitch_span if pitch_span is not None else _ALL_PITCHES[1],
            from_time if from_time is not None else 0.0,
            time_span if time_span is not None else _MAX_TIME_SPAN,
        )
    else:
        raise LiveAPIError("Provide note_ids or a region (or both bounds of one)")

    return {"note_count": len(_fetch_all_notes(clip))}
