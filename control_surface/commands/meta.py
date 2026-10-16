"""Session overview: the model's one-call map of the whole set."""

from typing import Any

from ..registry import REGISTRY, ParamSchema, ParamType
from ..utils.live_helpers import get_track
from .tracks import serialize_track
from .transport import _transport_state


@REGISTRY.register(
    "get_session_overview",
    params=[
        ParamSchema(
            "include_empty_slots",
            ParamType.BOOL,
            required=False,
            default=False,
            description="Empty clip slots are omitted by default (they dominate payload size)",
        ),
        ParamSchema("include_sends", ParamType.BOOL, required=False, default=False),
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Only this track in 'tracks' (returns, master, scenes still listed)",
        ),
    ],
    category="meta",
    read_only=True,
    description=(
        "One-shot map of the whole Live set: transport, every track with mixer "
        "state (volume/pan 0-1; volume 0.85 is 0 dB), devices and clip-slot "
        "summaries, return tracks, master, scenes. Call this first to orient. "
        "Notes are deliberately excluded — read them per-clip with get_notes. "
        "Only occupied clip slots are listed unless include_empty_slots."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "transport": {"type": "object"},
            "tracks": {"type": "array"},
            "return_tracks": {"type": "array"},
            "master_track": {"type": "object"},
            "scenes": {"type": "array"},
        },
    },
)
def get_session_overview(
    ctx,
    include_empty_slots: bool = False,
    include_sends: bool = False,
    track_index: int | None = None,
) -> dict[str, Any]:
    song = ctx.song
    if track_index is not None:
        tracks = [(track_index, get_track(song, track_index))]
    else:
        tracks = list(enumerate(song.tracks))
    return {
        "transport": _transport_state(song),
        "tracks": [
            serialize_track(
                i,
                t,
                "track",
                include_devices=True,
                include_clips=True,
                include_sends=include_sends,
                include_empty_slots=include_empty_slots,
            )
            for i, t in tracks
        ],
        "return_tracks": [
            serialize_track(i, t, "return", include_devices=True)
            for i, t in enumerate(song.return_tracks)
        ],
        "master_track": serialize_track(0, song.master_track, "master", include_devices=True),
        "scenes": [{"scene_index": i, "name": scene.name} for i, scene in enumerate(song.scenes)],
    }


@REGISTRY.register(
    "reload_commands",
    category="meta",
    internal=True,
    undo_step=False,
    description="Developer tool: reload command handlers from disk without restarting Live.",
)
def reload_commands(ctx) -> dict[str, Any]:
    # Registered here, inside the reloaded package, so it re-registers itself.
    from ..reloader import reload_command_modules

    return reload_command_modules()
