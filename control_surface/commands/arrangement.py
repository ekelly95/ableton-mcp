"""Arrangement view: the timeline where songs are built.

The workflow this enables: compose loops in Session view, then STAMP them onto
the timeline with place_clip_in_arrangement. There is no Live API to create an
empty MIDI clip directly in the arrangement — session-then-duplicate is the
only path (LOM-confirmed), and tool descriptions say so.

Arrangement clip indices are POSITIONAL (time-ordered) and shift when clips
are added/deleted — valid only against a fresh get_arrangement read. The
destructive delete takes an expected_start_time guard for exactly that reason.
"""

import os
from typing import Any, Dict, List, Optional

from ..config import AUDIO_EXTENSIONS, MAX_ARRANGEMENT_CLIPS_PER_READ, SAMPLES_DIR
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_arrangement_clip, get_track

_TIME_EPSILON = 1e-3


def _serialize_arrangement_clip(index: int, clip: Any) -> Dict[str, Any]:
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


def _track_arrangement(track_index: int, track: Any) -> Dict[str, Any]:
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
def get_arrangement(ctx, track_index: Optional[int] = None) -> Dict[str, Any]:
    song = ctx.song
    if track_index is not None:
        tracks = [_track_arrangement(track_index, get_track(song, track_index))]
    else:
        tracks = [_track_arrangement(i, t) for i, t in enumerate(song.tracks)]

    return {
        "tracks": tracks,
        "locators": [
            {"name": cue.name, "time": cue.time} for cue in song.cue_points
        ],
        "song_length": song.song_length,
        "record_mode": song.record_mode,
        "back_to_arranger": song.back_to_arranger,
        "arrangement_overdub": song.arrangement_overdub,
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
        "Copy a Session clip onto the Arrangement timeline. THE way to build a "
        "song: compose loops in session slots, stamp them here. Placing over an "
        "existing clip truncates it. There is no way to create MIDI directly in "
        "the arrangement — always go via a session clip. If the timeline sounds "
        "wrong afterwards, check back_to_arranger in the response: true means "
        "session clips are still overriding the timeline (fix via set_transport)."
    ),
)
def place_clip_in_arrangement(
    ctx, track_index: int, slot_index: int, destination_time: float
) -> Dict[str, Any]:
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
        "arrangement_clips": [
            _serialize_arrangement_clip(i, c) for i, c in enumerate(clips)
        ],
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
            min_value=0,
            description="Timeline position in beats",
        ),
    ],
    category="arrangement",
    description=(
        "Import an audio file from disk as a clip on the Arrangement timeline of "
        "an AUDIO track. The bridge half of sample generation: any tool that "
        f"writes an audio file (convention: under {SAMPLES_DIR}) can land it in "
        "the set with this. First import of a file may take a while (Live "
        "analyzes it). Arrangement-only — Live's API has no session-slot import."
    ),
)
def import_audio(ctx, track_index: int, file_path: str, position: float) -> Dict[str, Any]:
    track = get_track(ctx.song, track_index)

    if not os.path.isabs(file_path):
        raise LiveAPIError(
            f"file_path must be ABSOLUTE (got '{file_path}') — Live resolves "
            f"relative paths against its own install directory"
        )
    if not os.path.isfile(file_path):
        raise LiveAPIError(f"File not found: {file_path}")
    if not file_path.lower().endswith(AUDIO_EXTENSIONS):
        raise LiveAPIError(
            f"Unsupported extension. Use one of: {', '.join(AUDIO_EXTENSIONS)}"
        )
    if track.has_midi_input:
        raise LiveAPIError(
            f"Track {track_index} is a MIDI track — audio clips need an audio track "
            f"(create one with create_track type=audio)"
        )

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
    return {"imported": _serialize_arrangement_clip(index, clip)}


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
            required=False,
            min_value=0,
            description=(
                "Safety check: the start_time you saw for this clip. If the index "
                "has gone stale, the delete errors instead of removing the wrong clip."
            ),
        ),
    ],
    category="arrangement",
    destructive=True,
    description="Delete a clip from the timeline. Destructive — pass expected_start_time as a stale-index guard.",
)
def delete_arrangement_clip(
    ctx,
    track_index: int,
    arrangement_clip_index: int,
    expected_start_time: Optional[float] = None,
) -> Dict[str, Any]:
    track = get_track(ctx.song, track_index)
    clip = get_arrangement_clip(track, arrangement_clip_index)

    if expected_start_time is not None and abs(clip.start_time - expected_start_time) > _TIME_EPSILON:
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
def create_locator(ctx, time: float, name: Optional[str] = None) -> Dict[str, Any]:
    song = ctx.song

    # set_or_delete_cue TOGGLES at the playhead — creating where one exists
    # would silently DELETE it, so refuse instead.
    for cue in song.cue_points:
        if abs(cue.time - time) < _TIME_EPSILON:
            raise LiveAPIError(
                f"A locator already exists at {time} ('{cue.name}') — "
                f"set_or_delete_cue would remove it"
            )

    song.current_song_time = time
    song.set_or_delete_cue()

    created = next(
        (c for c in song.cue_points if abs(c.time - time) < _TIME_EPSILON), None
    )
    if created is None:
        raise LiveAPIError(f"Locator at {time} could not be confirmed")

    if name is not None:
        created.name = name  # VERIFY: CuePoint.name settable via API

    return {
        "locator": {"name": created.name, "time": created.time},
        "locators": [{"name": c.name, "time": c.time} for c in song.cue_points],
    }
