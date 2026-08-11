"""Session overview: the model's one-call map of the whole set."""

from typing import Any, Dict

from ..registry import REGISTRY
from .tracks import serialize_track
from .transport import _transport_state


@REGISTRY.register(
    "get_session_overview",
    params=[],
    category="meta",
    read_only=True,
    description=(
        "One-shot map of the whole Live set: transport, every track with clip-slot "
        "summaries, return tracks, master, scenes. Call this first to orient. "
        "Notes are deliberately excluded — read them per-clip with get_notes."
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
def get_session_overview(ctx) -> Dict[str, Any]:
    song = ctx.song
    return {
        "transport": _transport_state(song),
        "tracks": [
            serialize_track(i, t, "track", include_devices=True, include_clips=True)
            for i, t in enumerate(song.tracks)
        ],
        "return_tracks": [
            serialize_track(i, t, "return", include_devices=True)
            for i, t in enumerate(song.return_tracks)
        ],
        "master_track": serialize_track(0, song.master_track, "master", include_devices=True),
        "scenes": [
            {"scene_index": i, "name": scene.name} for i, scene in enumerate(song.scenes)
        ],
    }
