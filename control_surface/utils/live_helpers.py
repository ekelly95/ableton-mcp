"""Safe lookups into Live's object model. All raise LiveAPIError with a
message the model can act on (what was asked for, what exists)."""

from typing import Any

from ..errors import ValidationError
from ..registry import LiveAPIError
from .pitch import midi_to_pitch_name, pitch_to_midi


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


def clip_region_start(clip: Any) -> float:
    """Where the clip's audible region begins, in clip time (beats).

    clip.length is the loop length for a looped clip (end - start marker
    otherwise), but note and envelope times count from the clip's own zero —
    so a clip looped over 4-8 has length 4 and its notes at 4..8. Confirmed on
    Live 12.4.6.
    """
    return clip.loop_start if clip.looping else clip.start_marker


def clip_content_end(clip: Any) -> float:
    """The latest clip time that can hold notes or automation, in beats.

    Covers both the loop and the markers, so a default read never misses notes
    that sit past clip.length (see clip_region_start).
    """
    return max(clip.loop_end, clip.end_marker, clip_region_start(clip) + clip.length)


def get_device(track: Any, device_index: int) -> Any:
    devices = list(track.devices)
    if not 0 <= device_index < len(devices):
        raise LiveAPIError(
            f"Device index {device_index} out of range (track has {len(devices)} devices)"
        )
    return devices[device_index]


def resolve_chain(rack: Any, chain: Any) -> Any:
    """A chain inside a rack: a Drum Rack pad by note ('C1' or 36), or another
    rack's chain by index. Chains carry their own devices, so device tools work
    on them exactly as on a track."""
    if not getattr(rack, "can_have_chains", False):
        raise LiveAPIError(f"'{rack.name}' is not a rack — it has no chains")
    if getattr(rack, "can_have_drum_pads", False):
        note = pitch_to_midi(chain, param="chain")
        if not 0 <= note <= 127:
            raise ValidationError(f"Pad note must be 0-127, got {note}", param="chain")
        chains = list(rack.drum_pads[note].chains)
        if not chains:
            raise LiveAPIError(f"Pad {midi_to_pitch_name(note)} ({note}) in '{rack.name}' is empty")
        return chains[0]
    if isinstance(chain, bool) or not isinstance(chain, int):
        raise ValidationError(
            f"'{rack.name}' is not a Drum Rack: give chain as an index", param="chain"
        )
    chains = list(rack.chains)
    if not 0 <= chain < len(chains):
        raise LiveAPIError(
            f"Chain index {chain} out of range ('{rack.name}' has {len(chains)} chains)"
        )
    return chains[chain]


def resolve_device_host(
    song: Any,
    track_type: str,
    track_index: Any,
    rack_index: int | None = None,
    chain: Any = None,
) -> Any:
    """Where device tools operate: the track itself, or — with rack_index and
    chain — a chain inside one of its racks. Both may be equal-length lists
    for racks nested in chains, outermost first: Live's Core Library kits put
    an Instrument Rack inside every pad, so reaching a kick's Simpler takes
    rack_index [0, 0], chain ['C1', 0] (confirmed on Live 12.4.6)."""
    host = resolve_track(song, track_type, track_index)
    if rack_index is None and chain is None:
        return host
    if rack_index is None or chain is None:
        raise ValidationError("rack_index and chain go together", param="chain")
    racks = rack_index if isinstance(rack_index, list) else [rack_index]
    chains = chain if isinstance(chain, list) else [chain]
    if not racks or len(racks) != len(chains):
        raise ValidationError(
            "For nested racks give rack_index and chain as lists of the same length",
            param="chain",
        )
    for index, step in zip(racks, chains, strict=True):
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValidationError(f"rack_index entries must be device indices, got {index!r}")
        host = resolve_chain(get_device(host, index), step)
    return host


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
