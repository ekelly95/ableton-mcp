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

from ..config import AUDIO_EXTENSIONS, MAX_ARRANGEMENT_CLIPS_PER_READ, SEEK_EPSILON
from ..errors import ValidationError
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import (
    get_arrangement_clip,
    get_clip,
    get_clip_slot,
    get_take_lane,
    get_track,
)

# Exact-match tolerance for confirming/guarding clip and cue positions.
_TIME_EPSILON = 1e-3
# Wider than _TIME_EPSILON: Live may snap a new cue slightly off the
# requested time, so confirming one needs more slack than an exact match.
_CUE_SNAP_EPSILON = 0.05


def _serialize_arrangement_clip(index: int, clip: Any) -> dict[str, Any]:
    # end_time and is_audio_clip are omitted deliberately: end = start+length,
    # audio = not is_midi_clip. Derivable fields at 153 chars/clip added up to
    # tens of thousands of characters on full-song reads.
    return {
        "arrangement_clip_index": index,
        "name": clip.name,
        "start_time": clip.start_time,
        "length": clip.length,
        "is_midi_clip": clip.is_midi_clip,
        "looping": clip.looping,
    }


def _serialize_locators(song: Any) -> list[dict[str, Any]]:
    return [{"name": cue.name, "time": cue.time} for cue in song.cue_points]


def _find_clip_at(track: Any, time: float, want: Any = None) -> tuple[int, Any] | None:
    """(index, clip) whose start_time is within _TIME_EPSILON of `time`, or None.

    The confirm-by-rescan used after Live calls that return nothing useful
    (the arrangement clip list is time-ordered); `want` optionally filters,
    e.g. to MIDI clips only.
    """
    for i, clip in enumerate(track.arrangement_clips):
        if abs(clip.start_time - time) < _TIME_EPSILON and (want is None or want(clip)):
            return i, clip
    return None


def _track_arrangement(track_index: int, track: Any) -> dict[str, Any]:
    clips = list(track.arrangement_clips)
    truncated = len(clips) > MAX_ARRANGEMENT_CLIPS_PER_READ
    entry = {
        "track_index": track_index,
        "track_name": track.name,
        "arrangement_clips": [
            _serialize_arrangement_clip(i, c)
            for i, c in enumerate(clips[:MAX_ARRANGEMENT_CLIPS_PER_READ])
        ],
        "truncated": truncated,
    }
    # Take lanes: absent when the track has none (absent = default). LOM:
    # Track.take_lanes excludes the main lane (VERIFY at checkpoint).
    lanes = list(getattr(track, "take_lanes", []) or [])
    if lanes:
        entry["take_lanes"] = [
            {
                "take_lane_index": i,
                "name": lane.name,
                "clips": [
                    _serialize_arrangement_clip(j, c)
                    for j, c in enumerate(
                        list(lane.arrangement_clips)[:MAX_ARRANGEMENT_CLIPS_PER_READ]
                    )
                ],
            }
            for i, lane in enumerate(lanes)
        ]
    return entry


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
            "arrangement_overdub": {"type": "boolean"},
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
        "locators": _serialize_locators(song),
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
        ParamSchema(
            "take_lane_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description=(
                "Create the clip inside this take lane (from get_arrangement / "
                "create_take_lane) instead of the track's main lane"
            ),
        ),
    ],
    category="arrangement",
    description=(
        "Create an EMPTY MIDI clip directly on the Arrangement timeline of a "
        "MIDI track — in the main lane, or in a take lane via take_lane_index "
        "(stack variations side by side without timeline clutter). Then write "
        "notes into it with add_notes using arrangement_clip_index (+ the same "
        "take_lane_index)."
    ),
)
def create_arrangement_clip(
    ctx,
    track_index: int,
    start_time: float,
    length_beats: float,
    take_lane_index: int | None = None,
) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    if not track.has_midi_input:
        raise LiveAPIError(f"Track {track_index} is not a MIDI track")

    # Both routes confirm by re-scan: neither create_midi_clip documents a
    # return value. Take-lane clips must be created ON THE LANE OBJECT —
    # track-scoped clip APIs don't reach them (LOM; VERIFY at checkpoint).
    holder = get_take_lane(track, take_lane_index) if take_lane_index is not None else track
    try:
        holder.create_midi_clip(start_time, length_beats)
    except RuntimeError as e:
        # LOM: errors on frozen tracks, out-of-range times, or recording tracks
        raise LiveAPIError(f"Live refused to create the clip: {e}") from e

    placed = _find_clip_at(holder, start_time, lambda c: c.is_midi_clip)
    if placed is None:
        raise LiveAPIError(
            f"Clip creation at {start_time} could not be confirmed — re-read with get_arrangement"
        )
    index, clip = placed
    created = _serialize_arrangement_clip(index, clip)
    if take_lane_index is not None:
        created["take_lane_index"] = take_lane_index
    return {"created": created}


# Believed Live cap on non-main take lanes; enforced here so a runaway agent
# cannot create dozens of permanent lanes (VERIFY the real cap at checkpoint).
MAX_TAKE_LANES = 8


@REGISTRY.register(
    "create_take_lane",
    params=[
        ParamSchema("track_index", ParamType.INT, min_value=0),
        ParamSchema(
            "name",
            ParamType.STRING,
            required=False,
            description="Lane header name, e.g. 'Take 2 — sparse'",
        ),
    ],
    category="arrangement",
    description=(
        "Append a take lane to a track — a parallel arrangement lane for "
        "stacking MIDI variations side by side. CREATE SPARINGLY: Live has no "
        "API to delete a take lane, so every lane is permanent for this "
        "session (capped at 8 per track here for that reason)."
    ),
)
def create_take_lane(ctx, track_index: int, name: str | None = None) -> dict[str, Any]:
    track = get_track(ctx.song, track_index)
    lanes_attr = getattr(track, "take_lanes", None)
    if lanes_attr is None:
        raise LiveAPIError("This Live version exposes no take lanes on tracks")
    before = len(list(lanes_attr))
    if before >= MAX_TAKE_LANES:
        raise LiveAPIError(
            f"Track already has {before} take lanes (cap {MAX_TAKE_LANES}); "
            f"lanes cannot be deleted via the API — reuse an existing one"
        )
    try:
        track.create_take_lane()
    except RuntimeError as e:
        raise LiveAPIError(f"Live refused to create a take lane: {e}") from e
    lanes = list(track.take_lanes)
    if len(lanes) <= before:
        raise LiveAPIError("Take lane creation could not be confirmed")
    lane = lanes[-1]
    if name is not None:
        lane.name = name
    return {
        "take_lane_index": len(lanes) - 1,
        "name": lane.name,
        "take_lane_count": len(lanes),
    }


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
    output_schema={
        "type": "object",
        "properties": {
            "placed": {"type": "object"},
            "arrangement_clip_count": {"type": "integer"},
            "back_to_arranger": {"type": "boolean"},
        },
    },
)
def place_clip_in_arrangement(
    ctx, track_index: int, slot_index: int, destination_time: float
) -> dict[str, Any]:
    song = ctx.song
    track = get_track(song, track_index)
    session_clip = get_clip(track, slot_index)

    returned = track.duplicate_clip_to_arrangement(session_clip, destination_time)

    # LOM says the new clip is returned; fall back to an epsilon start-time
    # re-scan if a Live version hands back nothing.
    if returned is not None:
        index = next((i for i, c in enumerate(track.arrangement_clips) if c is returned), -1)
        placed = (index, returned)
    else:
        placed = _find_clip_at(track, destination_time)
    if placed is None:
        raise LiveAPIError(
            f"Clip placement at {destination_time} could not be confirmed — "
            f"re-read with get_arrangement"
        )

    # Only the placed clip plus a count: serializing the whole track's timeline
    # here grew without bound on real Sets (the read-path 500-clip cap never
    # applied to this write path) and could blow the response size limit AFTER
    # the placement had already succeeded.
    placed_index, placed_clip = placed
    return {
        "placed": _serialize_arrangement_clip(placed_index, placed_clip),
        "arrangement_clip_count": len(list(track.arrangement_clips)),
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
        return _import_to_session(track, track_index, slot_index, file_path)
    return _import_to_arrangement(track, file_path, position)


def _import_to_session(
    track: Any, track_index: int, slot_index: int, file_path: str
) -> dict[str, Any]:
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


def _import_to_arrangement(track: Any, file_path: str, position: float) -> dict[str, Any]:
    track.create_audio_clip(file_path, position)

    placed = _find_clip_at(track, position, lambda c: c.is_audio_clip)
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
    if abs(song.current_song_time - time) > SEEK_EPSILON:
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
        created = next((c for c in after if abs(c.time - time) < _CUE_SNAP_EPSILON), None)
    if created is None:
        raise LiveAPIError(f"Locator at {time} could not be confirmed")

    if name is not None:
        created.name = name  # CONFIRMED: checkpoint renames a cue to 'Chorus' and asserts it

    # Only the created locator: returning the whole list made the playbook's
    # drop-a-locator-per-section loop quadratic in payload. get_arrangement
    # still lists them all.
    return {"locator": {"name": created.name, "time": created.time}}
