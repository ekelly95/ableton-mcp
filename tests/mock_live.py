"""Mock of Ableton Live's API for testing without Live.

Encodes our best understanding of Live 12's Python API, including the modern
note-ID API (Live 11.1+). Facts marked VERIFY are assumptions to confirm at
the P4 real-Live checkpoint; corrections must be back-ported here with a
comment naming the verified behaviour.
"""

import itertools
import sys
import types
from typing import Any, List, Optional, Tuple

_note_id_counter = itertools.count(1)


class MockMidiNote:
    """Mirror of Live.Clip.MidiNote: mutable, identified by note_id.

    Real notes can only be OBTAINED from get_notes_extended, never constructed
    by scripts (VERIFY #4) — commands must fetch-modify-apply.
    """

    def __init__(
        self,
        pitch: int,
        start_time: float,
        duration: float,
        velocity: float = 100.0,
        mute: bool = False,
        probability: float = 1.0,
        velocity_deviation: float = 0.0,
        release_velocity: float = 64.0,
        note_id: Optional[int] = None,
    ):
        self.note_id = note_id if note_id is not None else next(_note_id_counter)
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute
        self.probability = probability
        self.velocity_deviation = velocity_deviation
        self.release_velocity = release_velocity


class MidiNoteSpecification:
    """Mirror of Live.Clip.MidiNoteSpecification (VERIFY #2: accepted kwargs)."""

    _ALLOWED = {
        "pitch",
        "start_time",
        "duration",
        "velocity",
        "mute",
        "probability",
        "velocity_deviation",
        "release_velocity",
    }

    def __init__(self, **kwargs):
        unknown = set(kwargs) - self._ALLOWED
        if unknown:
            raise TypeError(f"Unexpected keyword arguments: {unknown}")
        for required in ("pitch", "start_time", "duration"):
            if required not in kwargs:
                raise TypeError(f"Missing required argument: {required}")
        self.pitch = kwargs["pitch"]
        self.start_time = kwargs["start_time"]
        self.duration = kwargs["duration"]
        self.velocity = kwargs.get("velocity", 100.0)
        self.mute = kwargs.get("mute", False)
        self.probability = kwargs.get("probability", 1.0)
        self.velocity_deviation = kwargs.get("velocity_deviation", 0.0)
        self.release_velocity = kwargs.get("release_velocity", 64.0)


class MockParameter:
    def __init__(
        self,
        name: str,
        value: float = 0.5,
        min: float = 0.0,  # noqa: A002 - mirrors Live's attribute names
        max: float = 1.0,  # noqa: A002
        default_value: float = 0.5,
        is_quantized: bool = False,
    ):
        self.name = name
        self.value = value
        self.min = min
        self.max = max
        self.default_value = default_value
        self.is_quantized = is_quantized
        self.is_enabled = True

    def __str__(self) -> str:
        return f"{self.value:.2f}"


class MockClip:
    def __init__(self, length: float = 4.0, name: str = "", is_midi_clip: bool = True):
        self.name = name
        self.color_index = 0
        self.length = length
        self.looping = True
        self.loop_start = 0.0
        self.loop_end = length
        self.is_midi_clip = is_midi_clip
        self.is_audio_clip = not is_midi_clip
        self.is_playing = False
        self.is_recording = False
        self.signature_numerator = 4
        self.signature_denominator = 4
        self._notes: List[MockMidiNote] = []

    # --- Modern note-ID API (Live 11.1+) ---

    def get_notes_extended(
        self, from_pitch: int, pitch_span: int, from_time: float, time_span: float
    ) -> Tuple[MockMidiNote, ...]:
        """VERIFY #1: argument order is pitch-first (unlike legacy get_notes)."""
        return tuple(
            n
            for n in self._notes
            if from_pitch <= n.pitch < from_pitch + pitch_span
            and from_time <= n.start_time < from_time + time_span
        )

    def add_new_notes(self, specifications) -> None:
        """VERIFY #3: assumed to return None — callers re-fetch to learn IDs."""
        for spec in specifications:
            if not isinstance(spec, MidiNoteSpecification):
                raise TypeError("add_new_notes expects MidiNoteSpecification instances")
            self._notes.append(
                MockMidiNote(
                    pitch=spec.pitch,
                    start_time=spec.start_time,
                    duration=spec.duration,
                    velocity=spec.velocity,
                    mute=spec.mute,
                    probability=spec.probability,
                    velocity_deviation=spec.velocity_deviation,
                    release_velocity=spec.release_velocity,
                )
            )

    def apply_note_modifications(self, notes) -> None:
        """VERIFY #4: accepts notes previously fetched; matches by note_id."""
        by_id = {n.note_id: n for n in self._notes}
        for modified in notes:
            target = by_id.get(modified.note_id)
            if target is None:
                continue  # VERIFY: real Live silently ignores unknown ids?
            for attr in (
                "pitch",
                "start_time",
                "duration",
                "velocity",
                "mute",
                "probability",
                "velocity_deviation",
                "release_velocity",
            ):
                setattr(target, attr, getattr(modified, attr))

    def remove_notes_by_id(self, note_ids) -> None:
        """VERIFY #5: exact name/signature."""
        ids = set(note_ids)
        self._notes = [n for n in self._notes if n.note_id not in ids]

    def remove_notes_extended(
        self, from_pitch: int, pitch_span: int, from_time: float, time_span: float
    ) -> None:
        """VERIFY #5: pitch-first order assumed, matching get_notes_extended."""
        self._notes = [
            n
            for n in self._notes
            if not (
                from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span
            )
        ]


class MockClipSlot:
    def __init__(self, track: "MockTrack"):
        self._track = track
        self.has_clip = False
        self.clip: Optional[MockClip] = None
        self.is_playing = False
        self.is_triggered = False
        self.is_recording = False

    def create_clip(self, length: float) -> None:
        if self.has_clip:
            raise RuntimeError("Slot already has a clip")
        self.clip = MockClip(length=length, is_midi_clip=self._track.has_midi_input)
        self.has_clip = True

    def delete_clip(self) -> None:
        if not self.has_clip:
            raise RuntimeError("Slot has no clip")
        self.clip = None
        self.has_clip = False

    def fire(self) -> None:
        if self.has_clip:
            self.is_triggered = True
            self.clip.is_playing = True

    def stop(self) -> None:
        self.is_triggered = False
        if self.clip:
            self.clip.is_playing = False


class MockDevice:
    def __init__(self, name: str = "Device", class_name: str = "MockDevice"):
        self.name = name
        self.class_name = class_name
        self.is_active = True
        self.parameters: List[MockParameter] = [
            MockParameter("Device On", value=1.0, min=0.0, max=1.0, is_quantized=True),
            MockParameter("Macro 1", value=0.5),
            MockParameter("Macro 2", value=0.25),
        ]


class MockMixerDevice:
    def __init__(self, send_count: int = 0):
        # VERIFY #7: volume normalized 0-1 with 0.85 ~= 0 dB; pan -1..1
        self.volume = MockParameter("Volume", value=0.85, min=0.0, max=1.0, default_value=0.85)
        self.panning = MockParameter("Pan", value=0.0, min=-1.0, max=1.0, default_value=0.0)
        self.sends: List[MockParameter] = [
            MockParameter(f"Send {chr(65 + i)}", value=0.0) for i in range(send_count)
        ]


class MockTrack:
    def __init__(
        self,
        name: str = "Track",
        has_midi_input: bool = True,
        slot_count: int = 8,
        send_count: int = 0,
        can_be_armed: bool = True,
    ):
        self.name = name
        self.color_index = 0
        self.has_midi_input = has_midi_input
        self.has_audio_input = not has_midi_input
        self.can_be_armed = can_be_armed
        self.arm = False
        self.mute = False
        self.solo = False
        self.is_foldable = False
        self.is_grouped = False
        self.is_visible = True
        self.mixer_device = MockMixerDevice(send_count=send_count)
        self.clip_slots: List[MockClipSlot] = [MockClipSlot(self) for _ in range(slot_count)]
        self.devices: List[MockDevice] = []

    def stop_all_clips(self) -> None:
        for slot in self.clip_slots:
            slot.stop()

    def duplicate_clip_slot(self, slot_index: int) -> None:
        """VERIFY: assumed to copy the clip into the NEXT slot, failing if occupied."""
        source = self.clip_slots[slot_index]
        if not source.has_clip:
            raise RuntimeError("No clip to duplicate")
        target_index = slot_index + 1
        if target_index >= len(self.clip_slots):
            raise RuntimeError("No next slot")
        target = self.clip_slots[target_index]
        if target.has_clip:
            raise RuntimeError("Next slot is occupied")
        source_clip = source.clip
        target.create_clip(source_clip.length)
        clone = target.clip
        clone.name = source_clip.name
        clone.looping = source_clip.looping
        clone.loop_start = source_clip.loop_start
        clone.loop_end = source_clip.loop_end
        for n in source_clip._notes:
            clone._notes.append(
                MockMidiNote(
                    pitch=n.pitch,
                    start_time=n.start_time,
                    duration=n.duration,
                    velocity=n.velocity,
                    mute=n.mute,
                    probability=n.probability,
                    velocity_deviation=n.velocity_deviation,
                    release_velocity=n.release_velocity,
                )
            )


class MockScene:
    def __init__(self, name: str = ""):
        self.name = name
        self.is_triggered = False

    def fire(self) -> None:
        self.is_triggered = True


class MockSongView:
    def __init__(self):
        self.selected_track = None


class MockSong:
    def __init__(self, track_count: int = 2, scene_count: int = 4, return_count: int = 2):
        self.tempo = 120.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.is_playing = False
        self.metronome = False
        self.loop = False
        self.loop_start = 0.0
        self.loop_length = 4.0
        self.current_song_time = 0.0
        self.scenes: List[MockScene] = [MockScene(f"Scene {i + 1}") for i in range(scene_count)]
        self.tracks: List[MockTrack] = [
            MockTrack(name=f"{i + 1} Track", slot_count=scene_count, send_count=return_count)
            for i in range(track_count)
        ]
        self.return_tracks: List[MockTrack] = [
            MockTrack(name=f"{chr(65 + i)} Return", has_midi_input=False, slot_count=0, can_be_armed=False)
            for i in range(return_count)
        ]
        self.master_track = MockTrack(
            name="Master", has_midi_input=False, slot_count=0, can_be_armed=False
        )
        self.view = MockSongView()

    def start_playing(self) -> None:
        self.is_playing = True

    def stop_playing(self) -> None:
        self.is_playing = False

    def continue_playing(self) -> None:
        self.is_playing = True

    def stop_all_clips(self) -> None:
        for track in self.tracks:
            track.stop_all_clips()

    def create_midi_track(self, index: int) -> None:
        self.tracks.insert(index, MockTrack(name="MIDI", slot_count=len(self.scenes)))

    def create_audio_track(self, index: int) -> None:
        self.tracks.insert(
            index, MockTrack(name="Audio", has_midi_input=False, slot_count=len(self.scenes))
        )

    def create_return_track(self) -> None:
        """VERIFY #6: takes no index; appends."""
        self.return_tracks.append(
            MockTrack(name="Return", has_midi_input=False, slot_count=0, can_be_armed=False)
        )

    def delete_track(self, index: int) -> None:
        del self.tracks[index]

    def duplicate_track(self, index: int) -> None:
        source = self.tracks[index]
        copy = MockTrack(
            name=f"{source.name} Copy",
            has_midi_input=source.has_midi_input,
            slot_count=len(source.clip_slots),
        )
        self.tracks.insert(index + 1, copy)

    def create_scene(self, index: int) -> None:
        target = index if index >= 0 else len(self.scenes)
        self.scenes.insert(target, MockScene())
        for track in self.tracks:
            track.clip_slots.insert(target, MockClipSlot(track))

    def delete_scene(self, index: int) -> None:
        del self.scenes[index]
        for track in self.tracks:
            del track.clip_slots[index]


class MockApplication:
    def get_major_version(self) -> int:
        return 12

    def get_minor_version(self) -> int:
        return 4


class MockControlSurface:
    """Immediate scheduler: tasks run synchronously on the calling thread."""

    def __init__(self, song: Optional[MockSong] = None):
        self._song = song if song is not None else MockSong()
        self._app = MockApplication()
        self.messages: List[str] = []

    def schedule_message(self, delay, callback):
        callback()

    def song(self) -> MockSong:
        return self._song

    def application(self) -> MockApplication:
        return self._app

    def show_message(self, message: str) -> None:
        self.messages.append(message)


def install_mock_live() -> types.ModuleType:
    """Install a fake `Live` module so commands' lazy `import Live` works in tests."""
    live_module = types.ModuleType("Live")
    clip_module = types.ModuleType("Live.Clip")
    clip_module.MidiNoteSpecification = MidiNoteSpecification
    live_module.Clip = clip_module
    sys.modules["Live"] = live_module
    sys.modules["Live.Clip"] = clip_module
    return live_module


def uninstall_mock_live() -> None:
    sys.modules.pop("Live", None)
    sys.modules.pop("Live.Clip", None)
