"""Mock of Ableton Live's API for testing without Live.

Encodes our best understanding of Live 12's Python API, including the modern
note-ID API (Live 11.1+). Facts marked VERIFY are assumptions to confirm at
the P4 real-Live checkpoint; corrections must be back-ported here with a
comment naming the verified behaviour.
"""

import itertools
import sys
import types

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
        note_id: int | None = None,
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


class MockAutomationEnvelope:
    """Clip automation envelope. CONFIRMED by the 2026-08-11 spike on real
    Live 12.4.3: insert_step(time, length, value) + value_at_time round-trip
    exactly. Step model: value holds through [time, time+length); before any
    step the parameter's own value applies; after/between, the latest step's
    value holds (spike: value_at_time(1.5) returned step-2's value)."""

    def __init__(self, parameter: "MockParameter"):
        self.parameter = parameter
        self._steps = []  # (time, length, value), insertion order

    def insert_step(self, time: float, length: float, value: float) -> None:
        self._steps.append((float(time), float(length), float(value)))

    def value_at_time(self, time: float) -> float:
        # CONFIRMED on real Live 12.4.3 (2.2 checkpoint): steps are
        # START-EXCLUSIVE — at exactly a step's start time the PREVIOUS value
        # still reads, and at exactly 0.0 the parameter's current value reads.
        best = None
        for start, _length, value in self._steps:
            if start < time and (best is None or start >= best[0]):
                best = (start, value)
        return best[1] if best is not None else self.parameter.value


class MockNoteVector(list):
    """Stands in for Live's native MidiNoteVector.

    Verified in real Live 12.4: apply_note_modifications rejects plain Python
    tuples/lists of notes with a C++ signature error — only the vector object
    returned by get_notes_extended converts. The mock enforces the same rule.
    """


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
        # Arrangement placement (meaningful only for arrangement clips)
        self.start_time = 0.0
        self.end_time = length
        self.file_path: str | None = None
        self._notes: list[MockMidiNote] = []
        self._envelopes: dict = {}  # id(parameter) -> MockAutomationEnvelope

    # --- Clip automation envelopes (spike-CONFIRMED on real Live 12.4.3) ---

    def automation_envelope(self, parameter):
        """Returns None when no envelope exists for the parameter (CONFIRMED)."""
        return self._envelopes.get(id(parameter))

    def create_automation_envelope(self, parameter) -> MockAutomationEnvelope:
        env = MockAutomationEnvelope(parameter)
        self._envelopes[id(parameter)] = env
        return env

    def clear_envelope(self, parameter) -> None:
        """VERIFY at checkpoint: exact signature (assumed parameter arg)."""
        self._envelopes.pop(id(parameter), None)

    def clear_all_envelopes(self) -> None:
        """CONFIRMED (spike): no-arg call works."""
        self._envelopes.clear()

    @property
    def has_envelopes(self) -> bool:
        return bool(self._envelopes)

    # --- Modern note-ID API (Live 11.1+) ---

    def get_notes_extended(
        self, from_pitch: int, pitch_span: int, from_time: float, time_span: float
    ) -> "MockNoteVector":
        """CONFIRMED in real Live 12.4: argument order is pitch-first (the
        P4 region-removal step deleted exactly the right note)."""
        return MockNoteVector(
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
        """CONFIRMED in real Live 12.4: only accepts the native vector from
        get_notes_extended (a tuple raises a C++ signature error)."""
        if not isinstance(notes, MockNoteVector):
            raise TypeError(
                "Python argument types did not match C++ signature: "
                "apply_note_modifications expects the vector returned by "
                "get_notes_extended, not a Python tuple/list"
            )
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
        self.clip: MockClip | None = None
        self.is_playing = False
        self.is_triggered = False
        self.is_recording = False

    def create_clip(self, length: float) -> None:
        if self.has_clip:
            raise RuntimeError("Slot already has a clip")
        self.clip = MockClip(length=length, is_midi_clip=self._track.has_midi_input)
        self.has_clip = True

    def create_audio_clip(self, path: str) -> None:
        """LOM-confirmed: session audio import. Errors on non-audio tracks;
        empty-slot requirement assumed (VERIFY at checkpoint)."""
        if self._track.has_midi_input:
            raise RuntimeError("Clip slot doesn't belong to an audio track")
        if self.has_clip:
            raise RuntimeError("Slot already has a clip")
        clip = MockClip(length=1.0, name=path.rsplit("\\", 1)[-1], is_midi_clip=False)
        clip.file_path = path
        self.clip = clip
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
        self.parameters: list[MockParameter] = [
            MockParameter("Device On", value=1.0, min=0.0, max=1.0, is_quantized=True),
            MockParameter("Macro 1", value=0.5),
            MockParameter("Macro 2", value=0.25),
        ]


class MockMixerDevice:
    def __init__(self, send_count: int = 0):
        # VERIFY #7: volume normalized 0-1 with 0.85 ~= 0 dB; pan -1..1
        self.volume = MockParameter("Volume", value=0.85, min=0.0, max=1.0, default_value=0.85)
        self.panning = MockParameter("Pan", value=0.0, min=-1.0, max=1.0, default_value=0.0)
        self.sends: list[MockParameter] = [
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
        self.clip_slots: list[MockClipSlot] = [MockClipSlot(self) for _ in range(slot_count)]
        self.devices: list[MockDevice] = []
        self.arrangement_clips: list[MockClip] = []

    def stop_all_clips(self) -> None:
        for slot in self.clip_slots:
            slot.stop()

    def delete_device(self, device_index: int) -> None:
        del self.devices[device_index]

    def _insert_arrangement_clip(self, clip: MockClip) -> None:
        self.arrangement_clips.append(clip)
        # Live keeps arrangement_clips time-ordered, NOT creation-ordered
        # (LOM-confirmed; the fallback re-scan in place_clip relies on this).
        self.arrangement_clips.sort(key=lambda c: c.start_time)

    def create_midi_clip(self, start_time: float, length: float) -> None:
        """CONFIRMED in real Live 12.4: creates an empty MIDI clip directly in
        the arrangement (checkpoint wrote notes into one). Errors on
        non-MIDI/frozen tracks. Return undocumented; callers re-scan."""
        if not self.has_midi_input:
            raise RuntimeError("create_midi_clip called on a non-MIDI track")
        clip = MockClip(length=length, is_midi_clip=True)
        clip.start_time = start_time
        clip.end_time = start_time + length
        self._insert_arrangement_clip(clip)

    # Test hook: force the undocumented-return fallback path in place_clip.
    duplicate_returns_none = False

    def duplicate_clip_to_arrangement(self, clip: MockClip, destination_time: float):
        """The LOM does NOT document a return value (audit-verified — an earlier
        reviewer overstated this). The mock returns the clone by default and can
        return None via duplicate_returns_none to exercise the re-scan fallback.
        Real Live's behaviour: observed at checkpoint, see comment there."""
        clone = MockClip(length=clip.length, name=clip.name, is_midi_clip=clip.is_midi_clip)
        clone.looping = clip.looping
        clone.loop_start = clip.loop_start
        clone.loop_end = clip.loop_end
        clone.start_time = destination_time
        clone.end_time = destination_time + clip.length
        for n in clip._notes:
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
        self._insert_arrangement_clip(clone)
        if self.duplicate_returns_none:
            return None
        return clone

    def create_audio_clip(self, file_path: str, position: float) -> MockClip:
        """LOM-documented: absolute path to a supported audio file, arrangement only."""
        clip = MockClip(length=1.0, name=file_path.rsplit("\\", 1)[-1], is_midi_clip=False)
        clip.file_path = file_path
        clip.start_time = position
        clip.end_time = position + clip.length
        self._insert_arrangement_clip(clip)
        return clip

    def delete_clip(self, clip: MockClip) -> None:
        """CONFIRMED in real Live 12.4: accepts arrangement clip objects
        (checkpoint's guarded cleanup deletes passed)."""
        if clip in self.arrangement_clips:
            self.arrangement_clips.remove(clip)
        else:
            raise RuntimeError("Clip not on this track's arrangement")

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


class MockCuePoint:
    """Arrangement locator. CONFIRMED in real Live 12.4: name is settable
    (checkpoint renamed a cue to 'Chorus'). NOTE: current_song_time writes
    apply only AFTER the current scheduled task — hence create_locator's
    two-phase design; the mock applies them immediately, which is why the
    handler checks the playhead BEFORE writing (uniform behaviour)."""

    def __init__(self, time: float, name: str = ""):
        self.time = time
        self.name = name

    def jump(self) -> None:
        pass


class MockSongView:
    def __init__(self):
        self.selected_track = None


class MockSong:
    # Live's scale chooser list (Live 12.4) — the mock rejects names outside it
    # by silently keeping the old value, mirroring VERIFY-tagged Live behavior.
    KNOWN_SCALES = [
        "Major",
        "Minor",
        "Dorian",
        "Mixolydian",
        "Lydian",
        "Phrygian",
        "Locrian",
        "Whole Tone",
        "Half-whole Dim.",
        "Whole-half Dim.",
        "Minor Blues",
        "Minor Pentatonic",
        "Major Pentatonic",
        "Harmonic Minor",
        "Melodic Minor",
    ]
    SCALE_INTERVALS = {
        "Major": [0, 2, 4, 5, 7, 9, 11],
        "Minor": [0, 2, 3, 5, 7, 8, 10],
        "Dorian": [0, 2, 3, 5, 7, 9, 10],
    }

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
        self.root_note = 0
        self._scale_name = "Major"
        self.scale_mode = False
        self.record_mode = False
        self.back_to_arranger = False
        self.arrangement_overdub = False
        self.scenes: list[MockScene] = [MockScene(f"Scene {i + 1}") for i in range(scene_count)]
        self.tracks: list[MockTrack] = [
            MockTrack(name=f"{i + 1} Track", slot_count=scene_count, send_count=return_count)
            for i in range(track_count)
        ]
        self.return_tracks: list[MockTrack] = [
            MockTrack(
                name=f"{chr(65 + i)} Return", has_midi_input=False, slot_count=0, can_be_armed=False
            )
            for i in range(return_count)
        ]
        self.master_track = MockTrack(
            name="Main", has_midi_input=False, slot_count=0, can_be_armed=False
        )
        # CONFIRMED in real Live 12.4: the Main (master) track has no
        # mute/solo/arm properties — reading them raises. Mirror that.
        for missing_attr in ("mute", "solo", "arm"):
            delattr(self.master_track, missing_attr)
        self.view = MockSongView()
        self.cue_points: list[MockCuePoint] = []

    @property
    def scale_name(self) -> str:
        return self._scale_name

    @scale_name.setter
    def scale_name(self, value: str) -> None:
        # VERIFY: assumed Live silently keeps the old scale on unknown names
        # (the set_transport handler read-back turns that into a LiveAPIError).
        if value in self.KNOWN_SCALES:
            self._scale_name = value

    @property
    def scale_intervals(self):
        return self.SCALE_INTERVALS.get(self._scale_name, [0, 2, 4, 5, 7, 9, 11])

    @property
    def song_length(self) -> float:
        last = 0.0
        for track in self.tracks:
            for clip in track.arrangement_clips:
                last = max(last, clip.end_time)
        return last + 4.0

    def set_or_delete_cue(self) -> None:
        """TOGGLE at the current playhead: deletes an existing cue there."""
        for cue in list(self.cue_points):
            if abs(cue.time - self.current_song_time) < 1e-6:
                self.cue_points.remove(cue)
                return
        self.cue_points.append(MockCuePoint(time=self.current_song_time))

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


class MockBrowserItem:
    def __init__(self, name: str, children=None, is_loadable: bool = False):
        self.name = name
        self.children = children or []
        self.is_loadable = is_loadable
        self.is_folder = not is_loadable


def _default_browser_tree():
    return {
        "instruments": MockBrowserItem(
            "Instruments",
            children=[
                MockBrowserItem("Drift", is_loadable=True),
                MockBrowserItem("Operator", is_loadable=True),
                MockBrowserItem(
                    "Drum Rack",
                    children=[MockBrowserItem("Kit-Core 909", is_loadable=True)],
                ),
            ],
        ),
        "sounds": MockBrowserItem("Sounds", children=[]),
        "drums": MockBrowserItem("Drums", children=[]),
        "audio_effects": MockBrowserItem(
            "Audio Effects", children=[MockBrowserItem("Reverb", is_loadable=True)]
        ),
        "midi_effects": MockBrowserItem("MIDI Effects", children=[]),
        "samples": MockBrowserItem("Samples", children=[]),
        "packs": MockBrowserItem("Packs", children=[]),
        "user_library": MockBrowserItem("User Library", children=[]),
    }


class MockBrowser:
    """Loading targets the SELECTED track — mirrors Live's browser.load_item."""

    def __init__(self, song: MockSong):
        self._song = song
        for attr, item in _default_browser_tree().items():
            setattr(self, attr, item)

    def load_item(self, item: MockBrowserItem) -> None:
        if not item.is_loadable:
            raise RuntimeError(f"'{item.name}' is not loadable")
        target = self._song.view.selected_track
        if target is None:
            raise RuntimeError("No track selected")
        target.devices.append(MockDevice(name=item.name, class_name=item.name))


class MockApplication:
    def __init__(self, song: MockSong | None = None):
        self.browser = MockBrowser(song) if song is not None else None

    def get_major_version(self) -> int:
        return 12

    def get_minor_version(self) -> int:
        return 4


class MockControlSurface:
    """Immediate scheduler: tasks run synchronously on the calling thread."""

    def __init__(self, song: MockSong | None = None):
        self._song = song if song is not None else MockSong()
        self._app = MockApplication(self._song)
        self.messages: list[str] = []

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
