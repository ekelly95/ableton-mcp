"""Arrangement commands, scale settings, dual clip addressing, pitch names in flows."""

import wave
from pathlib import Path

import pytest

from control_surface.registry import LiveAPIError, ValidationError
from tests.conftest import run_command

MELODY = [
    {"pitch": "C3", "start_time": 0.0, "duration": 1.0},
    {"pitch": "Eb3", "start_time": 1.0, "duration": 1.0},
    {"pitch": 67, "start_time": 2.0, "duration": 2.0},
]


@pytest.fixture()
def with_session_clip(registry, ctx, song):
    run_command(registry, ctx, "create_clip", track_index=0, slot_index=0, length_beats=4.0)
    run_command(registry, ctx, "add_notes", track_index=0, slot_index=0, notes=MELODY)
    return song.tracks[0].clip_slots[0].clip


@pytest.fixture()
def wav_file(tmp_path) -> Path:
    path = tmp_path / "test_tone.wav"
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(22050)
        f.writeframes(b"\x00\x00" * 2205)
    return path


class TestScale:
    def test_scale_in_transport_state(self, registry, ctx, song):
        state = run_command(registry, ctx, "get_transport_state")
        assert state["scale"] == {
            "root": "C",
            "root_note": 0,
            "name": "Major",
            "scale_mode": False,
            "intervals": [0, 2, 4, 5, 7, 9, 11],
        }

    def test_set_scale_by_name(self, registry, ctx, song):
        state = run_command(
            registry, ctx, "set_transport", scale_root="D", scale_name="Minor", scale_mode=True
        )
        assert state["scale"]["root"] == "D"
        assert state["scale"]["name"] == "Minor"
        assert state["scale"]["scale_mode"] is True
        assert song.root_note == 2

    def test_set_scale_root_numeric(self, registry, ctx, song):
        run_command(registry, ctx, "set_transport", scale_root=7)
        assert song.root_note == 7

    def test_invalid_scale_name_surfaces(self, registry, ctx, song):
        with pytest.raises(LiveAPIError, match="rejected scale name"):
            run_command(registry, ctx, "set_transport", scale_name="Klingon Phrygian")

    def test_back_to_arranger_flag(self, registry, ctx, song):
        state = run_command(registry, ctx, "set_transport", back_to_arranger=False)
        assert state["back_to_arranger"] is False

    def test_record_mode_is_its_own_destructive_tool(self, registry, ctx, song):
        result = run_command(registry, ctx, "arrangement_record", enabled=True)
        assert result["record_mode"] is True
        assert song.record_mode is True
        run_command(registry, ctx, "arrangement_record", enabled=False)
        assert song.record_mode is False
        schema = registry.get("arrangement_record")
        assert schema.destructive is True
        assert "record_mode" not in {p.name for p in registry.get("set_transport").params}


class TestPitchNamesInNotes:
    def test_add_notes_with_names(self, registry, ctx, song, with_session_clip):
        notes = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        pitches = sorted(n["pitch"] for n in notes)
        # Ableton convention: C3=60, Eb3=63 (NOT 48/51 — that's the C4=60 world)
        assert pitches == [60, 63, 67]
        by_pitch = {n["pitch"]: n["pitch_name"] for n in notes}
        assert by_pitch[60] == "C3"
        assert by_pitch[63] == "D#3"

    def test_update_note_pitch_by_name(self, registry, ctx, song, with_session_clip):
        notes = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        target = notes[0]
        run_command(
            registry,
            ctx,
            "update_notes",
            track_index=0,
            slot_index=0,
            modifications=[{"note_id": target["note_id"], "pitch": "A3"}],
        )
        reread = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        edited = next(n for n in reread if n["note_id"] == target["note_id"])
        assert edited["pitch"] == 69
        assert edited["pitch_name"] == "A3"

    def test_bad_name_is_validation_error(self, registry, ctx, song, with_session_clip):
        with pytest.raises(ValidationError, match="Cannot parse pitch"):
            run_command(
                registry,
                ctx,
                "add_notes",
                track_index=0,
                slot_index=0,
                notes=[{"pitch": "X9", "start_time": 0, "duration": 1}],
            )


class TestArrangement:
    def test_place_clip(self, registry, ctx, song, with_session_clip):
        result = run_command(
            registry, ctx, "place_clip_in_arrangement",
            track_index=0, slot_index=0, destination_time=8.0,
        )
        assert result["placed"]["start_time"] == 8.0
        assert result["placed"]["end_time"] == 12.0
        assert result["placed"]["is_midi_clip"] is True
        assert len(result["arrangement_clips"]) == 1
        assert "back_to_arranger" in result

    def test_placed_clips_are_time_ordered(self, registry, ctx, song, with_session_clip):
        run_command(registry, ctx, "place_clip_in_arrangement", track_index=0, slot_index=0, destination_time=16.0)
        run_command(registry, ctx, "place_clip_in_arrangement", track_index=0, slot_index=0, destination_time=0.0)
        arrangement = run_command(registry, ctx, "get_arrangement", track_index=0)
        starts = [c["start_time"] for c in arrangement["tracks"][0]["arrangement_clips"]]
        assert starts == [0.0, 16.0]

    def test_edit_notes_in_arrangement_clip(self, registry, ctx, song, with_session_clip):
        run_command(registry, ctx, "place_clip_in_arrangement", track_index=0, slot_index=0, destination_time=0.0)
        notes = run_command(
            registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0
        )["notes"]
        assert len(notes) == 3
        run_command(
            registry,
            ctx,
            "update_notes",
            track_index=0,
            arrangement_clip_index=0,
            modifications=[{"note_id": notes[0]["note_id"], "velocity": 33}],
        )
        session_notes = run_command(registry, ctx, "get_notes", track_index=0, slot_index=0)["notes"]
        assert all(n["velocity"] != 33 for n in session_notes), "session copy must be untouched"

    def test_xor_addressing_enforced(self, registry, ctx, song, with_session_clip):
        with pytest.raises(ValidationError, match="exactly one"):
            run_command(registry, ctx, "get_notes", track_index=0)
        with pytest.raises(ValidationError, match="exactly one"):
            run_command(
                registry, ctx, "get_notes", track_index=0, slot_index=0, arrangement_clip_index=0
            )

    def test_delete_with_guard(self, registry, ctx, song, with_session_clip):
        run_command(registry, ctx, "place_clip_in_arrangement", track_index=0, slot_index=0, destination_time=4.0)
        with pytest.raises(LiveAPIError, match="Stale index"):
            run_command(
                registry, ctx, "delete_arrangement_clip",
                track_index=0, arrangement_clip_index=0, expected_start_time=99.0,
            )
        result = run_command(
            registry, ctx, "delete_arrangement_clip",
            track_index=0, arrangement_clip_index=0, expected_start_time=4.0,
        )
        assert result["remaining"] == 0

    def test_delete_guard_is_mandatory(self, registry, ctx, song, with_session_clip):
        run_command(registry, ctx, "place_clip_in_arrangement", track_index=0, slot_index=0, destination_time=4.0)
        with pytest.raises(ValidationError, match="Required parameter missing"):
            run_command(
                registry, ctx, "delete_arrangement_clip",
                track_index=0, arrangement_clip_index=0,
            )

    def test_create_arrangement_clip_direct(self, registry, ctx, song):
        result = run_command(
            registry, ctx, "create_arrangement_clip",
            track_index=0, start_time=16.0, length_beats=8.0,
        )
        assert result["created"]["start_time"] == 16.0
        assert result["created"]["is_midi_clip"] is True

        run_command(
            registry, ctx, "add_notes",
            track_index=0, arrangement_clip_index=0,
            notes=[{"pitch": "C3", "start_time": 0.0, "duration": 1.0}],
        )
        notes = run_command(
            registry, ctx, "get_notes", track_index=0, arrangement_clip_index=0
        )["notes"]
        assert notes[0]["pitch_name"] == "C3"

    def test_create_arrangement_clip_needs_midi_track(self, registry, ctx, song):
        song.tracks[1].has_midi_input = False
        with pytest.raises(LiveAPIError, match="not a MIDI track"):
            run_command(
                registry, ctx, "create_arrangement_clip",
                track_index=1, start_time=0.0, length_beats=4.0,
            )

    def test_place_clip_fallback_when_no_return(self, registry, ctx, song, with_session_clip):
        song.tracks[0].duplicate_returns_none = True
        result = run_command(
            registry, ctx, "place_clip_in_arrangement",
            track_index=0, slot_index=0, destination_time=12.0,
        )
        assert result["placed"]["start_time"] == 12.0

    def test_record_arm_refused_while_playing(self, registry, ctx, song):
        song.is_playing = True
        with pytest.raises(LiveAPIError, match="Stop playback first"):
            run_command(registry, ctx, "arrangement_record", enabled=True)
        # Disarming is always allowed
        run_command(registry, ctx, "arrangement_record", enabled=False)

    def test_locator_refused_while_playing(self, registry, ctx, song):
        song.is_playing = True
        with pytest.raises(LiveAPIError, match="stationary playhead"):
            run_command(registry, ctx, "create_locator", time=8.0)

    def test_locator_create_and_collision(self, registry, ctx, song):
        result = run_command(registry, ctx, "create_locator", time=32.0, name="Chorus")
        assert result["locator"] == {"name": "Chorus", "time": 32.0}
        with pytest.raises(LiveAPIError, match="already exists"):
            run_command(registry, ctx, "create_locator", time=32.0, name="Verse")
        arrangement = run_command(registry, ctx, "get_arrangement")
        assert arrangement["locators"] == [{"name": "Chorus", "time": 32.0}]


class TestImportAudio:
    @pytest.fixture()
    def audio_track(self, registry, ctx, song) -> int:
        return run_command(registry, ctx, "create_track", type="audio")["track_index"]

    def test_import_onto_audio_track(self, registry, ctx, song, wav_file, audio_track):
        result = run_command(
            registry, ctx, "import_audio",
            track_index=audio_track, file_path=str(wav_file), position=8.0,
        )
        assert result["imported"]["is_audio_clip"] is True
        assert result["imported"]["start_time"] == 8.0

    def test_relative_path_rejected(self, registry, ctx, song, audio_track):
        with pytest.raises(LiveAPIError, match="ABSOLUTE"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path="samples/x.wav", position=0.0,
            )

    def test_missing_file_rejected(self, registry, ctx, song, audio_track):
        with pytest.raises(LiveAPIError, match="not found"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path=r"C:\nope\missing.wav", position=0.0,
            )

    def test_bad_extension_rejected(self, registry, ctx, song, tmp_path, audio_track):
        bad = tmp_path / "notes.txt"
        bad.write_text("not audio")
        with pytest.raises(LiveAPIError, match="Unsupported extension"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path=str(bad), position=0.0,
            )

    def test_midi_track_rejected(self, registry, ctx, song, wav_file):
        with pytest.raises(LiveAPIError, match="audio track"):
            run_command(
                registry, ctx, "import_audio",
                track_index=0, file_path=str(wav_file), position=0.0,
            )

    def test_session_import(self, registry, ctx, song, wav_file, audio_track):
        result = run_command(
            registry, ctx, "import_audio",
            track_index=audio_track, file_path=str(wav_file), slot_index=0,
        )
        assert result["imported"]["view"] == "session"
        assert song.tracks[audio_track].clip_slots[0].has_clip

    def test_session_import_occupied_slot(self, registry, ctx, song, wav_file, audio_track):
        run_command(
            registry, ctx, "import_audio",
            track_index=audio_track, file_path=str(wav_file), slot_index=0,
        )
        with pytest.raises(LiveAPIError, match="already has a clip"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path=str(wav_file), slot_index=0,
            )

    def test_import_xor_enforced(self, registry, ctx, song, wav_file, audio_track):
        with pytest.raises(ValidationError, match="exactly one"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path=str(wav_file),
            )
        with pytest.raises(ValidationError, match="exactly one"):
            run_command(
                registry, ctx, "import_audio",
                track_index=audio_track, file_path=str(wav_file),
                position=0.0, slot_index=0,
            )
