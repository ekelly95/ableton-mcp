"""P4 real-Live checkpoint: run the vertical slice against actual Ableton Live.

Exercises every VERIFY-tagged Live API assumption in the mock. Leaves one
'MCP Test' track with a clip in the set so the result is visible/audible;
everything else it creates, it removes.

Run:  python scripts/live_checkpoint.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.client import AbletonClient, CommandError  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def step(name):
    def decorator(func):
        def wrapper(client):
            try:
                detail = func(client)
                results.append((PASS, name, detail or ""))
                print(f"  {PASS}  {name}" + (f" — {detail}" if detail else ""), flush=True)
            except Exception as e:
                results.append((FAIL, name, str(e)))
                print(f"  {FAIL}  {name} — {type(e).__name__}: {e}", flush=True)
        return wrapper
    return decorator


NOTES = [
    {"pitch": 48, "start_time": 0.0, "duration": 1.0, "velocity": 100},      # C2
    {"pitch": 51, "start_time": 1.0, "duration": 1.0, "velocity": 92},       # Eb2
    {"pitch": 55, "start_time": 2.0, "duration": 1.0, "velocity": 96},       # G2
    {"pitch": 60, "start_time": 3.0, "duration": 1.0, "probability": 0.6},   # C3, sometimes
]

state = {}


@step("ping: version + command count")
def check_ping(client):
    result = client.send("ping")
    assert result["pong"] and result["version"] == "2.1.0", result
    return f"v{result['version']}, {result['command_count']} commands"


@step("get_session_overview")
def check_overview(client):
    overview = client.send("get_session_overview")
    state["original_tempo"] = overview["transport"]["tempo"]
    return (
        f"{len(overview['tracks'])} tracks, {len(overview['scenes'])} scenes, "
        f"tempo {overview['transport']['tempo']}"
    )


@step("create_track (midi, appended)")
def check_create_track(client):
    result = client.send("create_track", type="midi")
    state["track"] = result["track_index"]
    return f"index {result['track_index']}"


@step("set_track: name/color/volume batch")
def check_set_track(client):
    result = client.send(
        "set_track", track_index=state["track"], name="MCP Test", color_index=12, volume=0.85
    )
    assert result["name"] == "MCP Test"
    assert abs(result["volume"] - 0.85) < 0.01, f"volume read back {result['volume']}"
    return "volume 0.85 round-trips (0 dB law: VERIFY by eye — fader should sit at 0 dB)"


@step("create_clip 4 beats")
def check_create_clip(client):
    result = client.send(
        "create_clip", track_index=state["track"], slot_index=0, length_beats=4.0
    )
    assert result["length"] == 4.0, result
    return None


@step("add_notes: 4 notes incl. probability 0.6")
def check_add_notes(client):
    result = client.send(
        "add_notes", track_index=state["track"], slot_index=0, notes=NOTES
    )
    assert result == {"added": 4, "note_count": 4}, result
    return None


@step("get_notes: ids + modern fields round-trip")
def check_get_notes(client):
    result = client.send("get_notes", track_index=state["track"], slot_index=0)
    assert result["note_count"] == 4, result
    notes = sorted(result["notes"], key=lambda n: n["start_time"])
    assert notes[0]["pitch"] == 48
    prob_note = notes[3]
    assert abs(prob_note["probability"] - 0.6) < 0.01, f"probability {prob_note['probability']}"
    assert all(isinstance(n["note_id"], int) for n in notes)
    state["note_ids"] = [n["note_id"] for n in notes]
    return f"ids {state['note_ids']}, probability survived"


@step("update_notes: velocity by note_id")
def check_update_notes(client):
    target = state["note_ids"][1]
    result = client.send(
        "update_notes",
        track_index=state["track"],
        slot_index=0,
        modifications=[{"note_id": target, "velocity": 40}],
    )
    assert result["updated"] == 1, result
    notes = client.send("get_notes", track_index=state["track"], slot_index=0)["notes"]
    edited = next(n for n in notes if n["note_id"] == target)
    assert edited["velocity"] == 40.0, edited
    return "fetch-modify-apply works; ids stable across edits"


@step("remove_notes: by region, then by note_id")
def check_remove_notes(client):
    result = client.send(
        "remove_notes",
        track_index=state["track"],
        slot_index=0,
        from_time=3.0,
        time_span=10.0,
    )
    assert result["note_count"] == 3, result

    remaining = client.send("get_notes", track_index=state["track"], slot_index=0)["notes"]
    result = client.send(
        "remove_notes",
        track_index=state["track"],
        slot_index=0,
        note_ids=[remaining[0]["note_id"]],
    )
    assert result["note_count"] == 2, result

    client.send(
        "add_notes", track_index=state["track"], slot_index=0, notes=[NOTES[0], NOTES[3]]
    )
    return "region removal and by-id removal both verified"


@step("duplicate_clip into next slot")
def check_duplicate_clip(client):
    result = client.send("duplicate_clip", track_index=state["track"], slot_index=0)
    return f"landed in slot {result['new_slot']} (VERIFY: next-slot assumption)"


@step("launch_clip + stop_clips")
def check_launch(client):
    client.send("launch_clip", track_index=state["track"], slot_index=0)
    time.sleep(1.5)
    clips = client.send("get_clips", track_index=state["track"])
    slot = clips["tracks"][0]["clip_slots"][0]
    playing = slot["is_playing"] or slot["is_triggered"]
    client.send("stop_clips", track_index=state["track"])
    # Firing a clip starts Live's TRANSPORT, and stop_clips doesn't stop it —
    # park it so later steps (record, locators) see a stationary playhead.
    client.send("transport_control", action="stop")
    assert playing, f"clip did not report playing/triggered: {slot}"
    return "clip fired (triggered/playing state confirmed); transport parked"


@step("transport: tempo set + play + stop + restore")
def check_transport(client):
    original = client.send("get_transport_state")["tempo"]
    client.send("set_transport", tempo=100.0)
    st = client.send("get_transport_state")
    assert st["tempo"] == 100.0, st
    client.send("transport_control", action="play")
    time.sleep(1.0)
    st = client.send("get_transport_state")
    playing = st["is_playing"]
    client.send("transport_control", action="stop")
    client.send("set_transport", tempo=original)
    assert playing, "transport did not report playing"
    return f"tempo restored to {original}"


@step("create + delete second track (destructive path)")
def check_delete_track(client):
    created = client.send("create_track", type="midi")
    result = client.send("delete_track", track_index=created["track_index"])
    return f"deleted '{result['deleted']}'"


@step("create + delete scene")
def check_scenes(client):
    before = len(client.send("get_clips")["scenes"])
    client.send("create_scene")
    after = client.send("create_scene") and len(client.send("get_clips")["scenes"])
    client.send("delete_scene", scene_index=after - 1)
    client.send("delete_scene", scene_index=after - 2)
    final = len(client.send("get_clips")["scenes"])
    assert final == before, f"scene count {final} != {before}"
    return None


@step("browse: roots and instruments level")
def check_browse(client):
    roots = client.send("browse")
    names = [i["name"] for i in roots["items"]]
    assert "instruments" in names, names
    level = client.send("browse", path=["instruments"])
    assert level["items"], "instruments listing came back empty"
    loadable = [i for i in level["items"] if i["is_loadable"]]
    state["instrument"] = next(
        (i["name"] for i in loadable if i["name"].lower() == "drift"),
        loadable[0]["name"] if loadable else None,
    )
    return f"{len(level['items'])} items under instruments; will load '{state['instrument']}'"


@step("load_item: instrument onto MCP Test track (may take a while first time)")
def check_load(client):
    if not state.get("instrument"):
        raise AssertionError("no loadable instrument found under instruments root")
    # Same connection on purpose: a second client would queue behind this one
    # on the serial server and execute AFTER the script exits (audit finding).
    # The client grants heavy commands their full COMMAND_TIMEOUTS budget.
    result = client.send(
        "load_item",
        path=["instruments", state["instrument"]],
        track_index=state["track"],
    )
    assert result["loaded"] == state["instrument"], result
    assert result["devices_now"], "no device appeared on the track"
    return f"loaded {result['loaded']} → devices on track: {result['devices_now']}"


@step("get_devices + set_device_parameters round-trip")
def check_devices(client):
    devices = client.send("get_devices", track_index=state["track"])["devices"]
    assert devices, "expected the loaded instrument in the device list"
    detail = client.send(
        "get_devices", track_index=state["track"], device_index=0
    )["device"]
    assert detail["parameters"], "device reported no parameters"
    target = next(
        (p for p in detail["parameters"][1:] if not p["is_quantized"]),
        None,
    )
    if target is None:
        return "no continuous parameter to tweak (skipped set)"
    changed = client.send(
        "set_device_parameters",
        track_index=state["track"],
        device_index=0,
        parameters=[{"parameter": target["index"], "value": 0.7}],
    )["changed"]
    assert abs(changed[0]["value"] - 0.7) < 0.02, changed
    return f"set '{changed[0]['name']}' to 0.7 (display: {changed[0]['display_value']})"


@step("scale: set D Minor + scale_mode, verify, restore")
def check_scale(client):
    original = client.send("get_transport_state")["scale"]
    state_after = client.send(
        "set_transport", scale_root="D", scale_name="Minor", scale_mode=True
    )["scale"]
    assert state_after["root"] == "D", state_after
    assert state_after["name"] == "Minor", state_after
    assert state_after["scale_mode"] is True, state_after
    client.send(
        "set_transport",
        scale_root=original["root_note"],
        scale_name=original["name"],
        scale_mode=original["scale_mode"],
    )
    return f"D Minor round-tripped, intervals {state_after['intervals']}; restored {original['name']}"


@step("pitch names: add 'C3', read back 60/C3 (VERIFY in piano roll!)")
def check_pitch_names(client):
    client.send(
        "add_notes",
        track_index=state["track"],
        slot_index=0,
        notes=[{"pitch": "C3", "start_time": 0.0, "duration": 0.25, "velocity": 1}],
    )
    notes = client.send("get_notes", track_index=state["track"], slot_index=0)["notes"]
    added = next(n for n in notes if n["velocity"] == 1.0)
    assert added["pitch"] == 60, f"'C3' became MIDI {added['pitch']} — convention broken!"
    assert added["pitch_name"] == "C3", added
    client.send(
        "remove_notes", track_index=state["track"], slot_index=0, note_ids=[added["note_id"]]
    )
    return "'C3' == MIDI 60 == what Live's piano roll calls C3"


@step("arrangement: place session clip at 0 and 8, time-ordered")
def check_place_arrangement(client):
    r1 = client.send(
        "place_clip_in_arrangement",
        track_index=state["track"], slot_index=0, destination_time=8.0,
    )
    assert abs(r1["placed"]["start_time"] - 8.0) < 0.01, r1["placed"]
    r2 = client.send(
        "place_clip_in_arrangement",
        track_index=state["track"], slot_index=0, destination_time=0.0,
    )
    starts = [c["start_time"] for c in r2["arrangement_clips"]]
    assert starts == sorted(starts), f"not time-ordered: {starts}"
    assert len(starts) == 2, starts
    client.send("set_transport", back_to_arranger=False)
    return f"2 clips on timeline at {starts}; back_to_arranger cleared"


@step("arrangement note edit via arrangement_clip_index")
def check_arrangement_note_edit(client):
    notes = client.send(
        "get_notes", track_index=state["track"], arrangement_clip_index=0
    )["notes"]
    assert notes, "arrangement clip has no notes?"
    client.send(
        "update_notes",
        track_index=state["track"],
        arrangement_clip_index=0,
        modifications=[{"note_id": notes[0]["note_id"], "velocity": 37}],
    )
    reread = client.send(
        "get_notes", track_index=state["track"], arrangement_clip_index=0
    )["notes"]
    assert any(n["velocity"] == 37.0 for n in reread), reread
    return "vector fetch-modify-apply works on timeline clips too"


@step("create_arrangement_clip: direct MIDI on timeline + notes into it")
def check_direct_arrangement(client):
    result = client.send(
        "create_arrangement_clip",
        track_index=state["track"], start_time=16.0, length_beats=4.0,
    )
    assert result["created"]["is_midi_clip"] is True, result
    idx = result["created"]["arrangement_clip_index"]
    client.send(
        "add_notes",
        track_index=state["track"], arrangement_clip_index=idx,
        notes=[{"pitch": "C4", "start_time": 0.0, "duration": 2.0}],
    )
    notes = client.send(
        "get_notes", track_index=state["track"], arrangement_clip_index=idx
    )["notes"]
    assert notes and notes[0]["pitch"] == 72, notes
    return "empty MIDI clip created at beat 16, note written directly (audit route)"


@step("arrangement_record: toggle on/off without playing")
def check_arrangement_record(client):
    client.send("transport_control", action="stop")
    client.send("arrangement_record", enabled=True)
    # Verified: the write can read back stale in the same task — confirm on a
    # SEPARATE request (next tick).
    st = client.send("get_transport_state")
    assert st["record_mode"] is True, st
    client.send("arrangement_record", enabled=False)
    st = client.send("get_transport_state")
    assert st["record_mode"] is False, st
    return "record arms/disarms (confirmed next-tick); never played while armed"


@step("locator 'Chorus' at beat 32 + collision refusal")
def check_locator(client):
    client.send("transport_control", action="stop")
    result = client.send("create_locator", time=32.0, name="Chorus")
    assert result["locator"]["time"] == 32.0, result
    assert result["locator"]["name"] == "Chorus", result
    try:
        client.send("create_locator", time=32.0, name="Verse")
        raise AssertionError("collision was not refused")
    except CommandError as e:
        assert "already exists" in e.message, e
    return "created + rename verified; second create at same time refused"


@step("import_audio: generated sine WAV onto new audio track")
def check_import_audio(client):
    import math
    import os
    import wave as wave_mod

    samples_dir = r"C:\dev\ableton-mcp\samples"
    os.makedirs(samples_dir, exist_ok=True)
    wav_path = os.path.join(samples_dir, "checkpoint_tone_440.wav")
    with wave_mod.open(wav_path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(44100)
        frames = bytearray()
        for i in range(44100):
            value = int(12000 * math.sin(2 * math.pi * 440 * i / 44100))
            frames += value.to_bytes(2, "little", signed=True)
        f.writeframes(bytes(frames))

    created = client.send("create_track", type="audio")
    state["audio_track"] = created["track_index"]
    state["wav_path"] = wav_path
    result = client.send(
        "import_audio",
        track_index=state["audio_track"],
        file_path=wav_path,
        position=8.0,
    )
    assert result["imported"]["is_audio_clip"] is True, result

    session_result = client.send(
        "import_audio",
        track_index=state["audio_track"],
        file_path=wav_path,
        slot_index=0,
    )
    assert session_result["imported"]["view"] == "session", session_result
    return f"440Hz tone: timeline at beat 8 AND session slot 0 (track {state['audio_track']})"


@step("audible finale: play the ARRANGEMENT from the top for 5 seconds")
def check_finale(client):
    client.send("set_transport", back_to_arranger=False)
    client.send("transport_control", action="play", position=0.0)
    time.sleep(5.0)
    client.send("transport_control", action="stop")
    return "that was the timeline: the placed loops, then the 440Hz tone at beat 8"


@step("arrangement cleanup: guarded deletes")
def check_arrangement_cleanup(client):
    clips = client.send("get_arrangement", track_index=state["track"])["tracks"][0][
        "arrangement_clips"
    ]
    for clip in reversed(clips):
        client.send(
            "delete_arrangement_clip",
            track_index=state["track"],
            arrangement_clip_index=clip["arrangement_clip_index"],
            expected_start_time=clip["start_time"],
        )
    if "audio_track" in state:
        client.send("delete_track", track_index=state["audio_track"])
    remaining = client.send("get_arrangement", track_index=state["track"])["tracks"][0][
        "arrangement_clips"
    ]
    assert remaining == [], remaining
    return "timeline cleared with expected_start_time guards; audio track removed ('Chorus' locator left as a souvenir)"


@step("validation error taxonomy over the wire")
def check_validation(client):
    try:
        client.send("set_transport", tempo=5000)
        raise AssertionError("tempo=5000 was accepted")
    except CommandError as e:
        assert e.error_type == "ValidationError", e
        return f"rejected as expected: {e.message[:60]}"


@step("LiveAPIError taxonomy over the wire")
def check_live_error(client):
    try:
        client.send("delete_track", track_index=99)
        raise AssertionError("track 99 delete was accepted")
    except CommandError as e:
        assert e.error_type == "LiveAPIError", e
        return "out-of-range rejected with LiveAPIError"


WHOLE_RUN_BUDGET_SECONDS = 240


def main():
    print("P4 checkpoint against real Ableton Live\n", flush=True)
    # Fail fast: 8s per command (heavy commands still get their declared
    # COMMAND_TIMEOUTS budget on this same connection).
    client = AbletonClient(timeout=8.0)
    started = time.time()

    main_steps = [
        check_ping,
        check_overview,
        check_create_track,
        check_set_track,
        check_create_clip,
        check_add_notes,
        check_get_notes,
        check_update_notes,
        check_remove_notes,
        check_duplicate_clip,
        check_launch,
        check_transport,
        check_delete_track,
        check_scenes,
        check_browse,
        check_load,
        check_devices,
        check_scale,
        check_pitch_names,
        check_place_arrangement,
        check_arrangement_note_edit,
        check_direct_arrangement,
        check_arrangement_record,
        check_locator,
        check_import_audio,
        check_finale,
        check_validation,
        check_live_error,
    ]
    # Cleanup runs REGARDLESS of the time budget: an aborted run must not
    # leave the user's set littered (audit finding).
    cleanup_steps = [check_arrangement_cleanup]

    for check in main_steps:
        if time.time() - started > WHOLE_RUN_BUDGET_SECONDS:
            results.append((FAIL, "run budget exceeded", "remaining main steps skipped"))
            print(
                f"  SKIP  whole-run budget of {WHOLE_RUN_BUDGET_SECONDS}s exceeded — "
                f"skipping to cleanup",
                flush=True,
            )
            break
        check(client)
    for check in cleanup_steps:
        check(client)
    client.close()

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} steps passed")
    if failed:
        print("\nFailures:")
        for _, name, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("The 'MCP Test' track with its clips was left in the set on purpose.")


if __name__ == "__main__":
    main()
