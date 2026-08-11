"""Arrangement view: the timeline where songs are built.

Two composition routes (both LOM-confirmed): create an empty MIDI clip
directly on the timeline with create_arrangement_clip and write notes into it,
or compose loops in Session view and STAMP them with place_clip_in_arrangement.

Arrangement clip indices are POSITIONAL (time-ordered) and shift when clips
are added/deleted — valid only against a fresh get_arrangement read. The
destructive delete REQUIRES an expected_start_time guard for exactly that
reason.
"""

import os
from typing import Any

from ..config import AUDIO_EXTENSIONS, MAX_ARRANGEMENT_CLIPS_PER_READ
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_arrangement_clip, get_track

_TIME_EPSILON = 1e-3


def _serialize_arrangement_clip(index: int, clip: Any) -> dict[str, Any]:
    return {
        "arrangement_clip_index": index,
        "name": clip.name,
        "start_time": clip.start_time,
        "end_time": clip.end_time,
        "length": clip.length,
        "is_midi_clip": clip.is_midi_clip,
        "is_audio_clip": clip.is_audio_clip,
        "looping": clip.looping,
    }


def _track_arrangement(track_index: int, track: Any) -> dict[str, Any]:
    clips = list(track.arrangement_clips)
    truncated = len(clips) > MAX_ARRANGEMENT_CLIPS_PER_READ
    return {
        "track_index": track_index,
        "track_name": track.name,
        "arrangement_clips": [
            _serialize_arrangement_clip(i, c)
            for i, c in enumerate(clips[:MAX_ARRANGEMENT_CLIPS_PER_READ])
        ],
        "truncated": truncated,
    }


@REGISTRY.register(
    "get_arrangement",
    params=[
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Limit to one track; omit for all tracks",
        ),
    ],
    category="arrangement",
    read_only=True,
    description=(
        "The timeline: every track's arrangement clips (positional indices — "
        "only valid until the next change), locators, song length, record mode, "
        "back_to_arranger state."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "tracks": {"type": "array"},
            "locators": {"type": "array"},
            "song_length": {"type": "number"},
            "record_mode": {"type": "boolean"},
            "back_to_arranger": {"type": "boolean"},
        },
    },
)
def get_arrangement(ctx, track_index: int | None = None) -> dict[str, Any]:
    song = ctx.song
    if track_index is not None:
        tracks = [_track_arrangement(track_index, get_track(song, track_index))]
    else:
        tracks = [_track_arrangement(i, t) for i, t in enumerate(song.tracks)]

    return {
        "tracks": tracks,
        "locators": [{"name": cue.name, "time": cue.time} for cue in song.cue_points],
        "song_length": song.song_length,
        "record_mode": song.record_mode,
        "back_to_arranger": song.back_to_arranger,
        "arrangement_overdub": song.arrangement_overdub,
    }


@REGISTRY.register(
    "create_arrangement_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema(
            "start_time",
            ParamType.FLOAT,
            min_value=0,
            description="Timeline position in beats (bar N at 4/4 starts at (N-1)*4)",
        ),
        ParamSchema("length_beats", ParamType.FLOAT, min_value=0.25),
    ],
    category="arrangement",
    description=(
        "Create an EMPTY MIDI clip directly on the Arrangement timeline of a "
        "MIDI track, then write notes into it with add_notes using "
        "arrangement_clip_index. The direct composition route — no session "
        "slot needed."
    ),
)
def create_arrangement_clip(
    ctx, track_index: int, start_time: float, length_beats: float
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    if not track.has_midi_input:
        raise LiveAPIError(f"Track {track_index} is not a MIDI track")

    try:
        track.create_midi_clip(start_time, length_beats)
    except RuntimeError as e:
        # LOM: errors on frozen tracks, out-of-range times, or recording tracks
        raise LiveAPIError(f"Live refused to create the clip: {e}") from e

    clips = list(track.arrangement_clips)
    placed = next(
        (
            (i, c)
            for i, c in enumerate(clips)
            if abs(c.start_time - start_time) < _TIME_EPSILON and c.is_midi_clip
        ),
        None,
    )
    if placed is None:
        raise LiveAPIError(
            f"Clip creation at {start_time} could not be confirmed — re-read with get_arrangement"
        )
    index, clip = placed
    return {"created": _serialize_arrangement_clip(index, clip)}


@REGISTRY.register(
    "arrangement_record",
    params=[ParamSchema("enabled", ParamType.BOOL)],
    category="arrangement",
    destructive=True,
    description=(
        "Toggle the Arrangement Record button. DESTRUCTIVE: playing while this "
        "is on OVERWRITES the arrangement timeline with whatever happens in the "
        "session. Turn it off as soon as the take is done."
    ),
)
def arrangement_record(ctx, enabled: bool) -> dict[str, Any]:
    song = ctx.song
    if enabled and song.is_playing:
        raise LiveAPIError(
            "Transport is playing — arming record now would overwrite the "
            "arrangement immediately. Stop playback first (transport_control), "
            "then arm, then play to record deliberately."
        )
    song.record_mode = enabled
    # Verified on real Live 12.4: this write can read back STALE within the
    # same scheduled task. Report what was requested; confirm on the next
    # request (get_transport_state) if certainty is needed.
    return {
        "record_mode_requested": enabled,
        "record_mode": song.record_mode,
        "is_playing": song.is_playing,
    }


@REGISTRY.register(
    "place_clip_in_arrangement",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema(
            "slot_index",
            ParamType.INT,
            min_value=0,
            description="Session clip to copy onto the timeline",
        ),
        ParamSchema(
            "destination_time",
            ParamType.FLOAT,
            min_value=0,
            description="Timeline position in beats (bar N at 4/4 starts at (N-1)*4)",
        ),
    ],
    category="arrangement",
    description=(
        "Copy a Session clip onto the Arrangement timeline (the loop-then-stamp "
        "route; create_arrangement_clip is the direct route). Placing over an "
        "existing clip truncates it. If the timeline sounds wrong afterwards, "
        "check back_to_arranger in the response: true means session clips are "
        "still overriding the timeline (fix via set_transport)."
    ),
)
def place_clip_in_arrangement(
    ctx, track_index: int, slot_index: int, destination_time: float
) -> dict[str, Any]:
    from ..utils.live_helpers import get_clip

    song = ctx.song
    track = get_track(song, track_index)
    session_clip = get_clip(track, slot_index)

    returned = track.duplicate_clip_to_arrangement(session_clip, destination_time)

    # LOM says the new clip is returned; fall back to an epsilon start-time
    # re-scan if a Live version hands back nothing (list is time-ordered).
    placed = returned
    if placed is None:
        placed = next(
            (
                c
                for c in track.arrangement_clips
                if abs(c.start_time - destination_time) < _TIME_EPSILON
            ),
            None,
        )
    if placed is None:
        raise LiveAPIError(
            f"Clip placement at {destination_time} could not be confirmed — "
            f"re-read with get_arrangement"
        )

    clips = list(track.arrangement_clips)
    placed_index = next(
        (i for i, c in enumerate(clips) if c is placed),
        None,
    )

    return {
        "placed": _serialize_arrangement_clip(
            placed_index if placed_index is not None else -1, placed
        ),
        "arrangement_clips": [_serialize_arrangement_clip(i, c) for i, c in enumerate(clips)],
        "back_to_arranger": song.back_to_arranger,
    }


@REGISTRY.register(
    "import_audio",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema(
            "file_path",
            ParamType.STRING,
            description=f"ABSOLUTE path to an audio file ({', '.join(AUDIO_EXTENSIONS)})",
        ),
        ParamSchema(
            "position",
            ParamType.FLOAT,
            required=False,
            min_value=0,
            description="Arrangement route: timeline position in beats — exactly one of position / slot_index",
        ),
        ParamSchema(
            "slot_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Session route: empty clip slot on the audio track — exactly one of the two",
        ),
    ],
    category="arrangement",
    description=(
        "Import an audio file from disk onto an AUDIO track — either onto the "
        "Arrangement timeline (position, in beats) or into a Session slot "
        "(slot_index); give exactly one. The bridge half of sample generation: "
        "any tool that writes an audio file (convention: the project's "
        "samples/ folder) can land it in the set with this. First import of a "
        "file may take a while (Live analyzes it)."
    ),
)
def import_audio(
    ctx,
    track_index: int,
    file_path: str,
    position: float | None = None,
    slot_index: int | None = None,
) -> dict[str, Any]:
    from ..errors import ValidationError
    from ..utils.live_helpers import get_clip_slot

    if (position is None) == (slot_index is None):
        raise ValidationError(
            "Provide exactly one of position (arrangement) or slot_index (session)"
        )

    track = get_track(ctx.song, track_index)

    if not os.path.isabs(file_path):
        raise LiveAPIError(
            f"file_path must be ABSOLUTE (got '{file_path}') — Live resolves "
            f"relative paths against its own install directory"
        )
    if not os.path.isfile(file_path):
        raise LiveAPIError(f"File not found: {file_path}")
    if not file_path.lower().endswith(AUDIO_EXTENSIONS):
        raise LiveAPIError(f"Unsupported extension. Use one of: {', '.join(AUDIO_EXTENSIONS)}")
    if track.has_midi_input:
        raise LiveAPIError(
            f"Track {track_index} is a MIDI track — audio clips need an audio track "
            f"(create one with create_track type=audio)"
        )

    if slot_index is not None:
        slot = get_clip_slot(track, slot_index)
        if slot.has_clip:
            raise LiveAPIError(f"Slot {slot_index} already has a clip")
        slot.create_audio_clip(file_path)
        return {
            "imported": {
                "track_index": track_index,
                "slot_index": slot_index,
                "name": slot.clip.name if slot.has_clip else "",
                "is_audio_clip": True,
                "view": "session",
            }
        }

    track.create_audio_clip(file_path, position)

    clips = list(track.arrangement_clips)
    placed = next(
        (
            (i, c)
            for i, c in enumerate(clips)
            if abs(c.start_time - position) < _TIME_EPSILON and c.is_audio_clip
        ),
        None,
    )
    if placed is None:
        raise LiveAPIError("Import could not be confirmed — re-read with get_arrangement")

    index, clip = placed
    result = _serialize_arrangement_clip(index, clip)
    result["view"] = "arrangement"
    return {"imported": result}


@REGISTRY.register(
    "delete_arrangement_clip",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema(
            "arrangement_clip_index",
            ParamType.INT,
            min_value=0,
            description="Positional index from a FRESH get_arrangement read",
        ),
        ParamSchema(
            "expected_start_time",
            ParamType.FLOAT,
            required=True,
            min_value=0,
            description=(
                "REQUIRED safety check: the start_time you saw for this clip in "
                "get_arrangement. If the index has gone stale, the delete errors "
                "instead of removing the wrong clip."
            ),
        ),
    ],
    category="arrangement",
    destructive=True,
    description=(
        "Delete a clip from the timeline. Destructive. Indices go stale on any "
        "timeline change, so expected_start_time (from get_arrangement) is "
        "mandatory — a mismatch aborts the delete."
    ),
)
def delete_arrangement_clip(
    ctx,
    track_index: int,
    arrangement_clip_index: int,
    expected_start_time: float,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = get_arrangement_clip(track, arrangement_clip_index)

    if abs(clip.start_time - expected_start_time) > _TIME_EPSILON:
        raise LiveAPIError(
            f"Stale index: clip {arrangement_clip_index} starts at {clip.start_time}, "
            f"not {expected_start_time}. Re-read with get_arrangement."
        )

    name = clip.name
    start = clip.start_time
    track.delete_clip(clip)
    return {
        "deleted": name,
        "start_time": start,
        "remaining": len(list(track.arrangement_clips)),
    }


@REGISTRY.register(
    "create_locator",
    params=[
        ParamSchema("time", ParamType.FLOAT, min_value=0, description="Position in beats"),
        ParamSchema("name", ParamType.STRING, required=False),
    ],
    category="arrangement",
    description=(
        "Drop a named marker (locator) on the timeline — e.g. 'Chorus' at bar 9. "
        "Moves the playhead to that time as a side effect."
    ),
)
def create_locator(ctx, time: float, name: str | None = None) -> dict[str, Any]:
    song = ctx.song

    # Verified on real Live 12.4: with the transport running, the playhead
    # moves between our seek and the cue toggle, so the cue lands elsewhere
    # (or nowhere findable). Locators need a parked playhead.
    if song.is_playing:
        raise LiveAPIError(
            "Transport is playing — stop playback first (transport_control), "
            "locators need a stationary playhead."
        )

    # Verified on real Live 12.4: a current_song_time write does NOT take
    # effect within the same scheduled task (a cue toggled right after the
    # seek landed at the OLD playhead). So this command is two-phase: phase 1
    # issues the seek and returns {"phase": "seeking"}; the caller repeats the
    # call, and phase 2 (playhead now parked at the target) drops the cue.
    # The MCP server and the checkpoint loop on "seeking" transparently.
    if abs(song.current_song_time - time) > 0.01:
        song.current_song_time = time
        return {
            "phase": "seeking",
            "note": "Playhead seek issued; call create_locator again to place the cue.",
        }

    # set_or_delete_cue TOGGLES at the playhead — creating where one exists
    # would silently DELETE it, so refuse instead.
    before = list(song.cue_points)
    for cue in before:
        if abs(cue.time - time) < _TIME_EPSILON:
            raise LiveAPIError(
                f"A locator already exists at {time} ('{cue.name}') — "
                f"set_or_delete_cue would remove it"
            )

    song.set_or_delete_cue()

    # Confirm by list diff, not exact time: Live may snap the cue slightly.
    after = list(song.cue_points)
    created = next((c for c in after if c not in before), None)
    if created is None:
        created = next((c for c in after if abs(c.time - time) < 0.05), None)
    if created is None:
        raise LiveAPIError(f"Locator at {time} could not be confirmed")

    if name is not None:
        created.name = name  # VERIFY: CuePoint.name settable via API

    return {
        "locator": {"name": created.name, "time": created.time},
        "locators": [{"name": c.name, "time": c.time} for c in song.cue_points],
    }
