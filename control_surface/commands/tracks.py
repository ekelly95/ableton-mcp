"""Tracks: read, create, duplicate, delete, and the batch setter."""

from typing import Any

from ..errors import PartialApplyError
from ..registry import REGISTRY, LiveAPIError, ParamSchema, ParamType
from ..utils.live_helpers import get_track, resolve_track
from ..utils.normalize import denormalize_parameter, normalize_parameter


def _serialize_clip_summary(slot_index: int, slot: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "slot_index": slot_index,
        "has_clip": slot.has_clip,
        "is_playing": slot.is_playing,
        "is_triggered": slot.is_triggered,
    }
    if slot.has_clip:
        clip = slot.clip
        info["clip"] = {
            "name": clip.name,
            "length": clip.length,
            "looping": clip.looping,
            "is_midi_clip": clip.is_midi_clip,
            "is_playing": clip.is_playing,
            "color_index": clip.color_index,
        }
    return info


def serialize_track(
    index: int,
    track: Any,
    track_type: str = "track",
    include_devices: bool = False,
    include_clips: bool = False,
) -> dict[str, Any]:
    mixer = track.mixer_device
    info: dict[str, Any] = {
        "index": index,
        "type": track_type,
        "name": track.name,
        "color_index": track.color_index,
        "volume": normalize_parameter(mixer.volume),
        "pan": normalize_parameter(mixer.panning),
    }
    # Verified in real Live 12.4: the master ("Main") track has no mute/solo
    # properties at all — reading them raises.
    if track_type != "master":
        info["muted"] = track.mute
        info["soloed"] = track.solo
    if track_type == "track":
        info["is_midi"] = track.has_midi_input
        info["can_be_armed"] = track.can_be_armed
        info["armed"] = track.arm if track.can_be_armed else False
        info["sends"] = [
            {"index": i, "name": send.name, "value": normalize_parameter(send)}
            for i, send in enumerate(mixer.sends)
        ]
    if include_devices:
        info["devices"] = [
            {
                "index": i,
                "name": device.name,
                "class_name": device.class_name,
                "is_active": device.is_active,
            }
            for i, device in enumerate(track.devices)
        ]
    if include_clips:
        info["clip_slots"] = [
            _serialize_clip_summary(i, slot) for i, slot in enumerate(track.clip_slots)
        ]
    return info


@REGISTRY.register(
    "get_tracks",
    params=[
        ParamSchema("include_devices", ParamType.BOOL, required=False, default=False),
        ParamSchema("include_clips", ParamType.BOOL, required=False, default=False),
    ],
    category="tracks",
    read_only=True,
    description=(
        "All tracks with mixer state (volume/pan normalized 0-1; volume 0.85 is 0 dB), "
        "plus return tracks and master. Flags add device lists and clip-slot summaries."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "tracks": {"type": "array"},
            "return_tracks": {"type": "array"},
            "master_track": {"type": "object"},
            "track_count": {"type": "integer"},
        },
    },
)
def get_tracks(ctx, include_devices: bool = False, include_clips: bool = False) -> dict[str, Any]:
    song = ctx.song
    tracks = [
        serialize_track(i, t, "track", include_devices, include_clips)
        for i, t in enumerate(song.tracks)
    ]
    returns = [
        serialize_track(i, t, "return", include_devices, False)
        for i, t in enumerate(song.return_tracks)
    ]
    master = serialize_track(0, song.master_track, "master", include_devices, False)
    return {
        "tracks": tracks,
        "return_tracks": returns,
        "master_track": master,
        "track_count": len(tracks),
    }


@REGISTRY.register(
    "create_track",
    params=[
        ParamSchema("type", ParamType.STRING, enum_values=["midi", "audio", "return"]),
        ParamSchema(
            "index",
            ParamType.INT,
            required=False,
            default=-1,
            description="Insert position; -1 appends. Ignored for return tracks (Live always appends them).",
        ),
    ],
    category="tracks",
    description="Create a MIDI, audio, or return track.",
)
def create_track(ctx, type: str, index: int = -1) -> dict[str, Any]:  # noqa: A002
    song = ctx.song
    if type == "return":
        song.create_return_track()
        new_index = len(list(song.return_tracks)) - 1
        return {
            "track_index": new_index,
            "track_type": "return",
            "name": list(song.return_tracks)[new_index].name,
        }

    track_count = len(list(song.tracks))
    insert_index = track_count if index == -1 else max(0, min(index, track_count))
    if type == "midi":
        song.create_midi_track(insert_index)
    else:
        song.create_audio_track(insert_index)
    new_track = list(song.tracks)[insert_index]
    return {"track_index": insert_index, "track_type": type, "name": new_track.name}


@REGISTRY.register(
    "duplicate_track",
    params=[ParamSchema("track_index", ParamType.INT, min_value=0)],
    category="tracks",
    description="Duplicate a track (with its clips and devices). New track lands directly after the source.",
)
def duplicate_track(ctx, track_index: int) -> dict[str, Any]:
    song = ctx.song
    get_track(song, track_index)
    song.duplicate_track(track_index)
    new_index = track_index + 1
    return {"track_index": new_index, "name": list(song.tracks)[new_index].name}


@REGISTRY.register(
    "delete_track",
    params=[ParamSchema("track_index", ParamType.INT, min_value=0)],
    category="tracks",
    destructive=True,
    description="Delete a track and everything on it. Destructive.",
)
def delete_track(ctx, track_index: int) -> dict[str, Any]:
    song = ctx.song
    track = get_track(song, track_index)
    name = track.name
    song.delete_track(track_index)
    return {"deleted": name, "track_count": len(list(song.tracks))}


@REGISTRY.register(
    "set_track",
    params=[
        ParamSchema(
            "track_type",
            ParamType.STRING,
            required=False,
            default="track",
            enum_values=["track", "return", "master"],
        ),
        ParamSchema(
            "track_index",
            ParamType.INT,
            required=False,
            min_value=0,
            description="Required unless track_type is 'master'",
        ),
        ParamSchema("name", ParamType.STRING, required=False),
        ParamSchema("color_index", ParamType.INT, required=False, min_value=0, max_value=69),
        ParamSchema(
            "volume",
            ParamType.FLOAT,
            required=False,
            min_value=0,
            max_value=1,
            description="Normalized 0-1; 0.85 is 0 dB",
        ),
        ParamSchema(
            "pan",
            ParamType.FLOAT,
            required=False,
            min_value=0,
            max_value=1,
            description="Normalized 0-1; 0.5 is center",
        ),
        ParamSchema("arm", ParamType.BOOL, required=False),
        ParamSchema("mute", ParamType.BOOL, required=False),
        ParamSchema("solo", ParamType.BOOL, required=False),
        ParamSchema(
            "sends",
            ParamType.OBJECT_LIST,
            required=False,
            description="Batch send levels: [{index, value 0-1}]",
            item_schema={
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "minimum": 0},
                    "value": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["index", "value"],
            },
        ),
    ],
    category="tracks",
    description="Batch setter for one track's name, color, mixer, arm/mute/solo, and send levels — pass any subset in one call.",
)
def set_track(
    ctx,
    track_type: str = "track",
    track_index: int | None = None,
    name: str | None = None,
    color_index: int | None = None,
    volume: float | None = None,
    pan: float | None = None,
    arm: bool | None = None,
    mute: bool | None = None,
    solo: bool | None = None,
    sends: list | None = None,
) -> dict[str, Any]:
    song = ctx.song
    track = resolve_track(song, track_type, track_index)
    mixer = track.mixer_device

    # Every check that can fail runs BEFORE the first write, so a rejected
    # field (arm on a return track, sends on master, a bad send index) cannot
    # leave earlier fields already applied.
    if arm is not None and (track_type != "track" or not track.can_be_armed):
        raise LiveAPIError("This track cannot be armed")
    if mute is not None and track_type == "master":
        raise LiveAPIError("The master track cannot be muted")
    if solo is not None and track_type == "master":
        raise LiveAPIError("The master track cannot be soloed")
    resolved_sends: list[tuple[Any, float]] = []
    if sends is not None:
        if track_type == "master":
            raise LiveAPIError("The master track has no sends")
        send_params = list(mixer.sends)
        for item in sends:
            if "index" not in item or "value" not in item:
                raise LiveAPIError("Each send needs 'index' and 'value'")
            send_index = int(item["index"])
            if not 0 <= send_index < len(send_params):
                raise LiveAPIError(
                    f"Send index {send_index} out of range (track has {len(send_params)} sends)"
                )
            param = send_params[send_index]
            resolved_sends.append((param, denormalize_parameter(param, float(item["value"]))))

    applied: list[str] = []

    def _write(field: str, setter) -> None:
        try:
            setter()
        except Exception as e:
            raise PartialApplyError(field, str(e), applied) from e
        applied.append(field)

    if name is not None:
        _write("name", lambda: setattr(track, "name", name))
    if color_index is not None:
        _write("color_index", lambda: setattr(track, "color_index", color_index))
    if volume is not None:
        _write(
            "volume",
            lambda: setattr(mixer.volume, "value", denormalize_parameter(mixer.volume, volume)),
        )
    if pan is not None:
        _write(
            "pan",
            lambda: setattr(mixer.panning, "value", denormalize_parameter(mixer.panning, pan)),
        )
    if arm is not None:
        _write("arm", lambda: setattr(track, "arm", arm))
    if mute is not None:
        _write("mute", lambda: setattr(track, "mute", mute))
    if solo is not None:
        _write("solo", lambda: setattr(track, "solo", solo))
    for param, native in resolved_sends:
        _write(f"send '{param.name}'", lambda p=param, n=native: setattr(p, "value", n))

    return serialize_track(track_index if track_index is not None else 0, track, track_type)
