"""Record Live's own output (2.17): the Live-side steps behind capture_audio.

A temporary audio track takes the Resampling input (Live's master output),
records one session clip of a fixed length, and is removed afterwards; the
MCP server drives the steps and analyzes the file Live wrote.

Every step is its own command — its own main-thread task — on purpose: on
Live 12.4.6 creating an audio track, re-routing it and deleting it inside ONE
task crashed Live (access violation), while the same calls one task apart
were fine. Keep them separate.
"""

from typing import Any

from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType

CAPTURE_TRACK_NAME = "MCP Capture"
_MONITORING_OFF = 2  # LOM current_monitoring_state: 0 In, 1 Auto, 2 Off
_NO_QUANTIZATION = 0  # Live.Song.Quantization.q_no_q

_TRACK = ParamSchema("track_index", ParamType.INT, min_value=0)


def _capture_track(song: Any, track_index: int) -> Any:
    tracks = list(song.tracks)
    if not 0 <= track_index < len(tracks) or tracks[track_index].name != CAPTURE_TRACK_NAME:
        raise LiveAPIError(f"Track {track_index} is not the '{CAPTURE_TRACK_NAME}' track")
    return tracks[track_index]


@REGISTRY.register("capture_prepare", category="capture", internal=True)
def capture_prepare(ctx) -> dict[str, Any]:
    song = ctx.song
    if song.is_playing:
        raise LiveAPIError("Stop playback first — a capture starts the transport itself")
    song.create_audio_track(-1)
    track = list(song.tracks)[-1]
    track.name = CAPTURE_TRACK_NAME
    return {"track_index": len(list(song.tracks)) - 1}


@REGISTRY.register("capture_arm", params=[_TRACK], category="capture", internal=True)
def capture_arm(ctx, track_index: int) -> dict[str, Any]:
    track = _capture_track(ctx.song, track_index)
    resampling = next(
        (r for r in track.available_input_routing_types if r.display_name == "Resampling"), None
    )
    if resampling is None:
        raise LiveAPIError("This Live has no 'Resampling' input to record its output from")
    track.input_routing_type = resampling
    # Monitoring off: a Resampling track that monitored itself would feed back.
    track.current_monitoring_state = _MONITORING_OFF
    track.arm = True
    return {"input": track.input_routing_type.display_name, "armed": track.arm}


@REGISTRY.register(
    "capture_record",
    params=[_TRACK, ParamSchema("length_beats", ParamType.FLOAT, min_value=0.25)],
    category="capture",
    internal=True,
)
def capture_record(ctx, track_index: int, length_beats: float) -> dict[str, Any]:
    track = _capture_track(ctx.song, track_index)
    # ClipSlot.fire(record_length, launch_quantization) per Live 12.4.6's own
    # signature: record exactly length_beats, starting without waiting for a
    # bar line.
    list(track.clip_slots)[0].fire(record_length=length_beats, launch_quantization=_NO_QUANTIZATION)
    return {"recording": True, "tempo": ctx.song.tempo}


@REGISTRY.register(
    "capture_status", params=[_TRACK], category="capture", internal=True, read_only=True
)
def capture_status(ctx, track_index: int) -> dict[str, Any]:
    slot = list(_capture_track(ctx.song, track_index).clip_slots)[0]
    return {"has_clip": slot.has_clip, "recording": slot.is_recording}


@REGISTRY.register("capture_finish", params=[_TRACK], category="capture", internal=True)
def capture_finish(ctx, track_index: int) -> dict[str, Any]:
    song = ctx.song
    track = _capture_track(song, track_index)
    song.stop_playing()
    track.arm = False
    slot = list(track.clip_slots)[0]
    if not slot.has_clip:
        raise LiveAPIError("Nothing was recorded (no clip on the capture track)")
    clip = slot.clip
    return {"file_path": clip.file_path, "length_beats": clip.length}


@REGISTRY.register("capture_cleanup", params=[_TRACK], category="capture", internal=True)
def capture_cleanup(ctx, track_index: int) -> dict[str, Any]:
    song = ctx.song
    _capture_track(song, track_index)
    song.delete_track(track_index)
    return {"deleted": True}
