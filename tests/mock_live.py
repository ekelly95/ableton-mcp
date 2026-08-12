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
        is_quantized: bool = False,
        value_items: list[str] | None = None,
    ):
        self.name = name
        self._value = value
        self.min = min
        self.max = max
        self.is_quantized = is_quantized
        self.is_enabled = True
        # LOM: 0 = no automation, 1 = automation active, 2 = overridden.
        self.automation_state = 0
        if value_items is None and is_quantized:
            value_items = ["Off", "On"]
        # LOM: human-readable choices, quantized parameters only.
        self.value_items = list(value_items or [])

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, v: float) -> None:
        self._value = v
        # Direction confirmed by the LOM (automation_state, re_enable_
        # automation): writing a value while automation is active overrides
        # it. Exact trigger conditions VERIFY at checkpoint.
        if self.automation_state == 1:
            self.automation_state = 2

    def re_enable_automation(self) -> None:
        """LOM-documented: restore automation control for this parameter."""
        if self.automation_state == 2:
            self.automation_state = 1

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
        if not is_midi_clip:
            # CONFIRMED (LOM): `warping` exists on audio clips only — MIDI
            # clips raise AttributeError on access, so commands must guard with
            # is_audio_clip first. Default True for fresh audio clips is VERIFY
            # (checkpoint toggles it via set_clip warping=false).
            self.warping = True
        self.is_playing = False
        # Arrangement placement (meaningful only for arrangement clips)
        self.start_time = 0.0
        self.end_time = length
        # Set by MockTrack._insert_arrangement_clip — the single funnel every
        # arrangement clip passes through.
        self._is_arrangement_clip = False
        self.file_path: str | None = None
        self._notes: list[MockMidiNote] = []
        self._envelopes: dict = {}  # id(parameter) -> MockAutomationEnvelope

    # --- Clip automation envelopes (spike-CONFIRMED on real Live 12.4.3) ---

    def automation_envelope(self, parameter):
        """Returns None when no envelope exists for the parameter (CONFIRMED).
        CONFIRMED via Live's own API docstring: "Returns None for Arrangement
        clips." — arrangement clips hold only modulation; absolute automation
        lives on the track's automation lanes."""
        if self._is_arrangement_clip:
            return None
        return self._envelopes.get(id(parameter))

    def create_automation_envelope(self, parameter) -> "MockAutomationEnvelope | None":
        if self._is_arrangement_clip:
            # VERIFY: real behaviour on arrangement clips unknown (None vs
            # raise) — unreachable in production behind the envelope guard.
            return None
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
        self.parameters: list[MockParameter] = [
            MockParameter("Device On", value=1.0, min=0.0, max=1.0, is_quantized=True),
            MockParameter("Macro 1", value=0.5),
            MockParameter("Macro 2", value=0.25),
        ]

    @property
    def is_active(self) -> bool:
        # CONFIRMED (LOM): is_active is get/observe ONLY — assigning to it
        # raises here exactly as commands must expect. It reflects the
        # "Device On" switch (and on real Live also any enclosing Rack's
        # state); the writable control is the Device On parameter.
        on = next((p for p in self.parameters if p.name == "Device On"), None)
        return bool(on.value) if on is not None else True


class MockSimplerDevice(MockDevice):
    """LOM (Live 12.3.5 docs): SimplerDevice class-level properties + sample
    ops. CONFIRMED on real Live 12.4.3 (2.7 checkpoint): class_name IS
    'OriginalSimpler'; playback_mode/voices round-trip; reverse/warp_as/
    guess_playback_length callable (length came back in beats); on a one-shot
    808 sample the gates read can_warp_as=True, can_warp_double/half=False.
    Initial property DEFAULTS below remain assumptions (unprobed)."""

    def __init__(self, name: str = "Simpler"):
        super().__init__(name=name, class_name="OriginalSimpler")
        self.playback_mode = 0  # 0=Classic 1=One-Shot 2=Slicing (LOM)
        self.slicing_playback_mode = 0  # 0=Mono 1=Poly 2=Thru (LOM)
        self.retrigger = True
        self.pad_slicing = False
        self.voices = 8
        self.multi_sample_mode = False
        self.can_warp_as = True
        self.can_warp_double = True
        self.can_warp_half = False  # one gate False so tests cover refusal
        self.method_calls: list[tuple] = []

    def reverse(self) -> None:
        self.method_calls.append(("reverse",))

    def crop(self) -> None:
        self.method_calls.append(("crop",))

    def warp_as(self, beats: int) -> None:
        self.method_calls.append(("warp_as", beats))

    def warp_double(self) -> None:
        self.method_calls.append(("warp_double",))

    def warp_half(self) -> None:
        self.method_calls.append(("warp_half",))

    def guess_playback_length(self) -> float:
        # LOM: returns an estimated beat length between the markers.
        self.method_calls.append(("guess_playback_length",))
        return 4.0


class MockEq8Device(MockDevice):
    """LOM: Eq8Device class properties (global_mode 0=Stereo 1=L/R 2=M/S,
    edit_mode bool, oversample bool). CONFIRMED on real Live 12.4.3 (2.7
    checkpoint): class_name IS 'Eq8'; global_mode Stereo->M/S->Stereo and
    oversample round-trip."""

    def __init__(self, name: str = "EQ Eight"):
        super().__init__(name=name, class_name="Eq8")
        self.global_mode = 0
        self.edit_mode = False
        self.oversample = False


class MockDriftDevice(MockDevice):
    """LOM: DriftDevice pairs every <name>_index int with a <name>_list
    StringVector read at runtime — the bridge never hard-codes the labels.
    CONFIRMED on real Live 12.4.3 (2.7 checkpoint): class_name 'Drift';
    the pairing shape works; voice modes are Poly/Mono/Stereo/Unison and the
    lfo mod-source list is Env 1/Env 2/LFO/Key/Vel/Mod/Press/Slide (encoded
    below). _TARGETS and voice_count contents remain placeholders (unprobed —
    harmless, they are runtime-read)."""

    _SOURCES = ("Env 1", "Env 2", "LFO", "Key", "Vel", "Mod", "Press", "Slide")
    _TARGETS = ("None", "Osc 1 Pitch", "Filter Freq", "Shape", "Volume")

    def __init__(self, name: str = "Drift"):
        super().__init__(name=name, class_name="Drift")
        self.pitch_bend_range = 2
        for prop, items in (
            ("voice_mode", ("Poly", "Mono", "Stereo", "Unison")),
            ("voice_count", ("2", "4", "8", "16", "24", "32")),
            ("mod_matrix_pitch_source_1", self._SOURCES),
            ("mod_matrix_pitch_source_2", self._SOURCES),
            ("mod_matrix_filter_source_1", self._SOURCES),
            ("mod_matrix_filter_source_2", self._SOURCES),
            ("mod_matrix_shape_source", self._SOURCES),
            ("mod_matrix_lfo_source", self._SOURCES),
            ("mod_matrix_source_1", self._SOURCES),
            ("mod_matrix_source_2", self._SOURCES),
            ("mod_matrix_source_3", self._SOURCES),
            ("mod_matrix_target_1", self._TARGETS),
            ("mod_matrix_target_2", self._TARGETS),
            ("mod_matrix_target_3", self._TARGETS),
        ):
            setattr(self, f"{prop}_index", 0)
            setattr(self, f"{prop}_list", list(items))


class MockMixerDevice:
    def __init__(self, send_count: int = 0):
        # CONFIRMED on real Live 12.4 (2.2 checkpoint): the mixer parameters
        # are NAMED "Track Volume"/"Track Panning". Volume normalized 0-1 with
        # 0.85 ~= 0 dB; pan -1..1.
        self.volume = MockParameter("Track Volume", value=0.85, min=0.0, max=1.0)
        self.panning = MockParameter("Track Panning", value=0.0, min=-1.0, max=1.0)
        self.sends: list[MockParameter] = [
            MockParameter(f"Send {chr(65 + i)}", value=0.0) for i in range(send_count)
        ]


class MockTakeLane:
    """LOM (Live 12.3.5 docs): TakeLane has name (get/set/observe),
    arrangement_clips (get/observe), create_midi_clip(start_time, length),
    create_audio_clip(file_path, start_time). No delete function exists —
    lanes are permanent for the session.

    CONFIRMED on real Live 12.4.3 (2.7 checkpoint): create_take_lane appends,
    lane.name is settable, lane clip creation + note editing via the lane
    object work, and Track.arrangement_clips EXCLUDES lane clips.
    Still VERIFY: the exact lane cap (deliberately unprobed — probing would
    mint permanent lanes; we enforce 8 ourselves) and lane-clip list
    time-ordering with multiple clips per lane."""

    def __init__(self, track: "MockTrack", name: str = ""):
        self._track = track
        self.name = name
        self.arrangement_clips: list[MockClip] = []

    def create_midi_clip(self, start_time: float, length: float) -> None:
        # LOM: errors if the track is not MIDI, frozen, or recording.
        if not self._track.has_midi_input:
            raise RuntimeError("create_midi_clip called on a non-MIDI track's take lane")
        clip = MockClip(length=length, is_midi_clip=True)
        clip.start_time = start_time
        clip.end_time = start_time + length
        clip._is_arrangement_clip = True
        self.arrangement_clips.append(clip)
        # Assumed time-ordered like the track's own list (VERIFY at checkpoint).
        self.arrangement_clips.sort(key=lambda c: c.start_time)


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
        # Simplification: real Live derives this from routing (a MIDI track
        # only has audio output once an instrument sits on it); tests that
        # care set it explicitly.
        self.has_audio_output = True
        # LOM meters, 0.0-1.0 on Live's meter scale (NOT dB): level is the
        # 1-second hold peak (max of L/R, exists for audio and MIDI tracks);
        # left/right are momentary and exist only with audio output.
        self.output_meter_level = 0.0
        self.output_meter_left = 0.0
        self.output_meter_right = 0.0
        self.can_be_armed = can_be_armed
        self.arm = False
        self.mute = False
        self.solo = False
        self.mixer_device = MockMixerDevice(send_count=send_count)
        self.clip_slots: list[MockClipSlot] = [MockClipSlot(self) for _ in range(slot_count)]
        self.devices: list[MockDevice] = []
        self.arrangement_clips: list[MockClip] = []
        # Non-main take lanes only (CONFIRMED on real Live 12.4.3, 2.7
        # checkpoint: lane clips never appear in Track.arrangement_clips).
        self.take_lanes: list[MockTakeLane] = []

    # Believed Live cap on non-main lanes (VERIFY at checkpoint).
    MAX_TAKE_LANES = 8

    def create_take_lane(self) -> None:
        """LOM: no parameters, appends a lane; no delete exists. Behaviour at
        the cap is assumed to raise (VERIFY at checkpoint)."""
        if len(self.take_lanes) >= self.MAX_TAKE_LANES:
            raise RuntimeError("Take lane limit reached")
        self.take_lanes.append(MockTakeLane(self, name=f"Take {len(self.take_lanes) + 1}"))

    def stop_all_clips(self) -> None:
        for slot in self.clip_slots:
            slot.stop()

    def delete_device(self, device_index: int) -> None:
        del self.devices[device_index]

    # Native devices the mock "knows" for insert_device. The three with
    # class-property tables get their class-specific mocks (real class_name).
    KNOWN_NATIVE_DEVICES = (
        "Reverb",
        "EQ Eight",
        "Compressor",
        "Delay",
        "Operator",
        "Drift",
        "Simpler",
    )
    _DEVICE_FACTORIES = {
        "EQ Eight": MockEq8Device,
        "Drift": MockDriftDevice,
        "Simpler": MockSimplerDevice,
    }

    _INSTRUMENT_DEVICE_NAMES = {"Operator", "Drift", "Simpler"}
    _INSTRUMENT_CLASSES = {"Operator", "Drift", "OriginalSimpler", "InstrumentVector"}

    def insert_device(self, device_name: str, target_index: int = -1) -> None:
        """Track.insert_device, Live 12.3+ (LOM-documented; native devices
        only). VERIFY at checkpoint: unknown-name behaviour (assumed to raise)
        and return value (assumed None — callers re-scan the chain)."""
        if device_name not in self.KNOWN_NATIVE_DEVICES:
            raise RuntimeError(f"Unknown device: {device_name}")
        # CONFIRMED on real Live 12.4.3 (2.7 checkpoint): inserting a second
        # instrument raises "Invalid insert index for device 'X': Device
        # chains cannot have more than one instrument each."
        if device_name in self._INSTRUMENT_DEVICE_NAMES and any(
            d.class_name in self._INSTRUMENT_CLASSES for d in self.devices
        ):
            raise RuntimeError(
                f"Invalid insert index for device '{device_name}': Device chains "
                f"cannot have more than one instrument each."
            )
        factory = self._DEVICE_FACTORIES.get(device_name)
        if factory is not None:
            device = factory()
        else:
            device = MockDevice(name=device_name, class_name=device_name.replace(" ", ""))
        if target_index < 0 or target_index >= len(self.devices):
            self.devices.append(device)
        else:
            self.devices.insert(target_index, device)

    def _insert_arrangement_clip(self, clip: MockClip) -> None:
        clip._is_arrangement_clip = True
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
    (checkpoint renamed a cue to 'Chorus'). current_song_time writes apply
    only AFTER the current scheduled task — hence the two-phase designs in
    create_locator and transport_control; the mock now defers identically
    (see MockSong.current_song_time)."""

    def __init__(self, time: float, name: str = ""):
        self.time = time
        self.name = name


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
        self._current_song_time = 0.0
        self._pending_song_time: float | None = None
        self.root_note = 0
        self._scale_name = "Major"
        self.scale_mode = False
        self.record_mode = False
        self.back_to_arranger = False
        self.arrangement_overdub = False
        self.last_play_call: str | None = None
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
    def current_song_time(self) -> float:
        return self._current_song_time

    @current_song_time.setter
    def current_song_time(self, value: float) -> None:
        # CONFIRMED on real Live 12.4: a current_song_time write does NOT take
        # effect within the same scheduled task (a cue toggled right after a
        # seek landed at the OLD playhead). The mock defers identically:
        # MockControlSurface.schedule_message applies the pending seek at the
        # next task boundary, so handlers reading it back in-task see stale.
        self._pending_song_time = float(value)

    def _apply_pending_song_time(self) -> None:
        if self._pending_song_time is not None:
            self._current_song_time = self._pending_song_time
            self._pending_song_time = None

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
        # CONFIRMED on real Live 12.4.3 (2.3 checkpoint): starts from the
        # INSERT/START MARKER, not current_song_time — a play-from-position
        # landed at the marker, not the seek target. transport_control
        # therefore calls continue_playing after an explicit seek. The mock
        # records the verb for tests but does not model the marker itself.
        self.last_play_call = "start"
        self.is_playing = True

    def stop_playing(self) -> None:
        self.is_playing = False

    def continue_playing(self) -> None:
        self.last_play_call = "continue"
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
        # Mirrors the real shape that makes plugins special: the plugin item is
        # loadable AND has children (Live-indexed presets), so is_folder is False
        # while children is non-empty.
        "plugins": MockBrowserItem(
            "Plug-Ins",
            children=[
                MockBrowserItem(
                    "VST3",
                    children=[
                        MockBrowserItem(
                            "Omnisphere",
                            children=[MockBrowserItem("Factory Preset A", is_loadable=True)],
                            is_loadable=True,
                        ),
                    ],
                ),
            ],
        ),
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


class MockControlSurface:
    """Immediate scheduler: tasks run synchronously on the calling thread."""

    def __init__(self, song: MockSong | None = None):
        self._song = song if song is not None else MockSong()
        self._app = MockApplication(self._song)

    def schedule_message(self, delay, callback):
        # Pending playhead seeks apply BETWEEN scheduled tasks on real Live
        # (repo-verified on 12.4) — model that at the task boundary so
        # two-phase commands behave in tests exactly as against Live.
        self._song._apply_pending_song_time()
        callback()

    def song(self) -> MockSong:
        return self._song

    def application(self) -> MockApplication:
        return self._app


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
