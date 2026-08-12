"""Safe lookups into Live's object model. All raise LiveAPIError with a
message the model can act on (what was asked for, what exists)."""

from typing import Any

from ..errors import ValidationError
from ..registry import LiveAPIError


def get_track(song: Any, track_index: int) -> Any:
    tracks = list(song.tracks)
    if not 0 <= track_index < len(tracks):
        raise LiveAPIError(
            f"Track index {track_index} out of range (song has {len(tracks)} tracks)"
        )
    return tracks[track_index]


def get_return_track(song: Any, index: int) -> Any:
    returns = list(song.return_tracks)
    if not 0 <= index < len(returns):
        raise LiveAPIError(
            f"Return track index {index} out of range (song has {len(returns)} return tracks)"
        )
    return returns[index]


def resolve_track(song: Any, track_type: str, track_index: Any) -> Any:
    if track_type == "master":
        return song.master_track
    if track_index is None:
        raise LiveAPIError(f"track_index is required for track_type '{track_type}'")
    if track_type == "return":
        return get_return_track(song, track_index)
    return get_track(song, track_index)


def get_scene(song: Any, scene_index: int) -> Any:
    scenes = list(song.scenes)
    if not 0 <= scene_index < len(scenes):
        raise LiveAPIError(
            f"Scene index {scene_index} out of range (song has {len(scenes)} scenes)"
        )
    return scenes[scene_index]


def get_clip_slot(track: Any, slot_index: int) -> Any:
    slots = list(track.clip_slots)
    if not 0 <= slot_index < len(slots):
        raise LiveAPIError(f"Slot index {slot_index} out of range (track has {len(slots)} slots)")
    return slots[slot_index]


def get_clip(track: Any, slot_index: int) -> Any:
    slot = get_clip_slot(track, slot_index)
    if not slot.has_clip:
        raise LiveAPIError(f"Slot {slot_index} has no clip")
    return slot.clip


def get_arrangement_clip(track: Any, index: int) -> Any:
    clips = list(track.arrangement_clips)
    if not 0 <= index < len(clips):
        raise LiveAPIError(
            f"Arrangement clip index {index} out of range (track has {len(clips)} "
            f"arrangement clips). Indices are positional — re-read with get_arrangement."
        )
    return clips[index]


def get_take_lane(track: Any, take_lane_index: int) -> Any:
    lanes = list(getattr(track, "take_lanes", []) or [])
    if not 0 <= take_lane_index < len(lanes):
        raise LiveAPIError(
            f"Take lane index {take_lane_index} out of range (track has {len(lanes)} "
            f"take lanes — create one with create_take_lane)"
        )
    return lanes[take_lane_index]


def get_take_lane_clip(lane: Any, index: int) -> Any:
    clips = list(lane.arrangement_clips)
    if not 0 <= index < len(clips):
        raise LiveAPIError(
            f"Clip index {index} out of range (take lane has {len(clips)} clips). "
            f"Indices are positional within the lane — re-read with get_arrangement."
        )
    return clips[index]


def resolve_clip_ref(
    track: Any,
    slot_index: int | None,
    arrangement_clip_index: int | None,
    require_midi: bool = False,
    take_lane_index: int | None = None,
) -> Any:
    """One clip from a session slot, the arrangement, or a take lane.

    Exactly one of slot_index / arrangement_clip_index must be given — the
    schema can't express that (no oneOf in our tool generator), so it's
    enforced here. take_lane_index redirects arrangement_clip_index to count
    within that lane's clips instead of the track's main-lane clips.
    """
    if take_lane_index is not None and arrangement_clip_index is None:
        raise ValidationError(
            "take_lane_index needs arrangement_clip_index too (the clip's "
            "position within that lane, from get_arrangement's take_lanes)"
        )
    if (slot_index is None) == (arrangement_clip_index is None):
        raise ValidationError(
            "Provide exactly one of slot_index (session clip) or "
            "arrangement_clip_index (timeline clip, from get_arrangement)"
        )
    if slot_index is not None:
        clip = get_clip(track, slot_index)
    elif take_lane_index is not None:
        clip = get_take_lane_clip(get_take_lane(track, take_lane_index), arrangement_clip_index)
    else:
        clip = get_arrangement_clip(track, arrangement_clip_index)
    if require_midi and not clip.is_midi_clip:
        raise LiveAPIError("That clip is not a MIDI clip")
    return clip


def get_device(track: Any, device_index: int) -> Any:
    devices = list(track.devices)
    if not 0 <= device_index < len(devices):
        raise LiveAPIError(
            f"Device index {device_index} out of range (track has {len(devices)} devices)"
        )
    return devices[device_index]


# How many parameter names an unknown-name error lists as suggestions.
PARAM_SUGGESTION_CAP = 30


def resolve_device_parameter(device: Any, selector: Any) -> Any:
    """A device parameter by integer index or case-insensitive name."""
    params = list(device.parameters)
    if isinstance(selector, int) or (isinstance(selector, str) and str(selector).isdigit()):
        idx = int(selector)
        if not 0 <= idx < len(params):
            raise LiveAPIError(
                f"Parameter index {idx} out of range (device has {len(params)} parameters)"
            )
        return params[idx]
    match = next((p for p in params if p.name.lower() == str(selector).lower()), None)
    if match is None:
        names = [p.name for p in params[:PARAM_SUGGESTION_CAP]]
        raise LiveAPIError(f"No parameter named '{selector}'. Available: {names}")
    return match
