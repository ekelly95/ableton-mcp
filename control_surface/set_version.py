"""A fingerprint of the set's structure (2.18). Not hot-reloaded (the socket
server holds it); changes here need a Live restart.

Covers what positional indices depend on — tracks (and their order and
names), each track's devices, which session slots hold clips, arrangement
clip positions, return and master devices, scene count. Parameter values,
mixer levels and clip contents are left out on purpose: a tweaked knob shifts
no index. Reading it costs a few LOM reads per track, inside the same
main-thread task as the command it accompanies.
"""

import hashlib
from typing import Any


def _devices(track: Any) -> tuple:
    return tuple((d.class_name, d.name) for d in track.devices)


def set_version(song: Any) -> str:
    digest = hashlib.blake2s(digest_size=6)

    def add(*parts: Any) -> None:
        digest.update(repr(parts).encode("utf-8"))

    for track in song.tracks:
        add(
            "track",
            track.name,
            _devices(track),
            tuple(slot.has_clip for slot in track.clip_slots),
            tuple(round(clip.start_time, 4) for clip in track.arrangement_clips),
        )
    for track in song.return_tracks:
        add("return", track.name, _devices(track))
    add("master", _devices(song.master_track))
    add("scenes", len(list(song.scenes)))
    return digest.hexdigest()
