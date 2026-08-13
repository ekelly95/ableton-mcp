"""Real-Live checkpoint: run the vertical slice against actual Ableton Live.

Exercises every VERIFY-tagged Live API assumption in the mock. Leaves one
'MCP Test' track with a clip in the set so the result is visible/audible;
everything else it creates, it removes.

The Simpler step loads a specific drum sample from the author's User Library
(see check_simpler_class_props) — on another machine, edit that path to any
.wav in your own library, or the step fails. Making the checkpoint
self-contained is a recorded roadmap item.

Run:  python scripts/live_checkpoint.py
"""

import functools
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wav_util import write_sine_wav  # noqa: E402

from control_surface.config import SAMPLES_DIR, VERSION  # noqa: E402
from mcp_server.client import AbletonClient, CommandError  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
WHOLE_RUN_BUDGET_SECONDS = 300  # raised for the 2.7 take-lane + device steps
results = []


def step(name):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(client):
            # The result is recorded from the STEP's outcome alone; printing
            # happens after, so a console hiccup can never flip a passed step
            # to FAIL (a cp1252 console once choked on '→' in a detail string
            # and double-counted the step).
            try:
                detail = func(client)
            except Exception as e:
                results.append((FAIL, name, str(e)))
                print(f"  {FAIL}  {name} — {type(e).__name__}: {e}", flush=True)
                return
            results.append((PASS, name, detail or ""))
            print(f"  {PASS}  {name}" + (f" — {detail}" if detail else ""), flush=True)

        return wrapper

    return decorator


NOTES = [
    {"pitch": 48, "start_time": 0.0, "duration": 1.0, "velocity": 100},  # C2
    {"pitch": 51, "start_time": 1.0, "duration": 1.0, "velocity": 92},  # Eb2
    {"pitch": 55, "start_time": 2.0, "duration": 1.0, "velocity": 96},  # G2
    {"pitch": 60, "start_time": 3.0, "duration": 1.0, "probability": 0.6},  # C3, sometimes
]

state = {}


@step("ping: version + command count")
def check_ping(client):
    result = client.send("ping")
    assert result["pong"] and result["version"] == VERSION, result
    return f"v{result['version']}, {result['command_count']} commands"


@step("get_session_overview")
def check_overview(client):
    overview = client.send("get_session_overview")
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
    result = client.send("create_clip", track_index=state["track"], slot_index=0, length_beats=4.0)
    assert result["length"] == 4.0, result
    return None


@step("add_notes: 4 notes incl. probability 0.6")
def check_add_notes(client):
    result = client.send("add_notes", track_index=state["track"], slot_index=0, notes=NOTES)
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

    client.send("add_notes", track_index=state["track"], slot_index=0, notes=[NOTES[0], NOTES[3]])
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
    client.send("create_scene")
    after = len(client.send("get_clips")["scenes"])
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
    # on the serial server and execute AFTER the script exits.
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
    detail = client.send("get_devices", track_index=state["track"], device_index=0)["device"]
    assert detail["parameters"], "device reported no parameters"
    # Since 2.5, is_quantized is ABSENT when false (absent = default).
    target = next(
        (p for p in detail["parameters"][1:] if not p.get("is_quantized", False)),
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
    state_after = client.send("set_transport", scale_root="D", scale_name="Minor", scale_mode=True)[
        "scale"
    ]
    assert state_after["root"] == "D", state_after
    assert state_after["name"] == "Minor", state_after
    assert state_after["scale_mode"] is True, state_after
    client.send(
        "set_transport",
        scale_root=original["root_note"],
        scale_name=original["name"],
        scale_mode=original["scale_mode"],
    )
    return (
        f"D Minor round-tripped, intervals {state_after['intervals']}; restored {original['name']}"
    )


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
        track_index=state["track"],
        slot_index=0,
        destination_time=8.0,
    )
    assert abs(r1["placed"]["start_time"] - 8.0) < 0.01, r1["placed"]
    r2 = client.send(
        "place_clip_in_arrangement",
        track_index=state["track"],
        slot_index=0,
        destination_time=0.0,
    )
    # The write path returns a count (never the full clip list — that response
    # was unbounded); the ordering assertion reads via get_arrangement.
    assert r2["arrangement_clip_count"] == 2, r2
    listing = client.send("get_arrangement", track_index=state["track"])
    starts = [c["start_time"] for c in listing["tracks"][0]["arrangement_clips"]]
    assert starts == sorted(starts), f"not time-ordered: {starts}"
    assert len(starts) == 2, starts
    client.send("set_transport", back_to_arranger=False)
    return f"2 clips on timeline at {starts}; back_to_arranger cleared"


@step("arrangement note edit via arrangement_clip_index")
def check_arrangement_note_edit(client):
    notes = client.send("get_notes", track_index=state["track"], arrangement_clip_index=0)["notes"]
    assert notes, "arrangement clip has no notes?"
    client.send(
        "update_notes",
        track_index=state["track"],
        arrangement_clip_index=0,
        modifications=[{"note_id": notes[0]["note_id"], "velocity": 37}],
    )
    reread = client.send("get_notes", track_index=state["track"], arrangement_clip_index=0)["notes"]
    assert any(n["velocity"] == 37.0 for n in reread), reread
    return "vector fetch-modify-apply works on timeline clips too"


@step("create_arrangement_clip: direct MIDI on timeline + notes into it")
def check_direct_arrangement(client):
    result = client.send(
        "create_arrangement_clip",
        track_index=state["track"],
        start_time=16.0,
        length_beats=4.0,
    )
    assert result["created"]["is_midi_clip"] is True, result
    idx = result["created"]["arrangement_clip_index"]
    client.send(
        "add_notes",
        track_index=state["track"],
        arrangement_clip_index=idx,
        notes=[{"pitch": "C4", "start_time": 0.0, "duration": 2.0}],
    )
    notes = client.send("get_notes", track_index=state["track"], arrangement_clip_index=idx)[
        "notes"
    ]
    assert notes and notes[0]["pitch"] == 72, notes
    return "empty MIDI clip created at beat 16, note written directly (direct-write route)"


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


@step("clip envelopes: LP sweep write/read + mixer volume + guarded clear")
def check_envelopes(client):
    ramp = [
        {"time": 0.0, "value": 1.0},
        {"time": 1.0, "value": 0.7},
        {"time": 2.0, "value": 0.4},
        {"time": 3.0, "value": 0.12},
    ]
    result = client.send(
        "set_clip_envelope",
        track_index=state["track"],
        slot_index=0,
        device_index=0,
        parameter="LP Freq",
        points=ramp,
    )
    assert result["points_written"] == 4, result

    read = client.send(
        "get_clip_envelope",
        track_index=state["track"],
        slot_index=0,
        device_index=0,
        parameter="LP Freq",
        samples=5,
    )
    assert read["exists"] is True, read
    values = [p["value"] for p in read["points"]]
    assert values[0] > values[2] > values[3], f"sweep not descending: {values}"

    client.send(
        "set_clip_envelope",
        track_index=state["track"],
        slot_index=0,
        mixer_parameter="volume",
        points=[{"time": 0.0, "value": 0.85}],
    )
    cleared = client.send(
        "clear_clip_envelopes",
        track_index=state["track"],
        slot_index=0,
        mixer_parameter="volume",
    )
    # clear_envelope(param) signature CONFIRMED; Live names it "Track Volume"
    assert cleared == {"cleared": "Track Volume"}, cleared
    gone = client.send(
        "get_clip_envelope",
        track_index=state["track"],
        slot_index=0,
        mixer_parameter="volume",
    )
    assert gone["exists"] is False, gone
    return "LP sweep written+verified (left in for the finale!); volume env set+cleared"


@step("locator 'Chorus' at a free beat + collision refusal")
def check_locator(client):
    client.send("transport_control", action="stop")
    # Idempotent across runs: previous runs leave their locator as a souvenir,
    # so find a beat nothing occupies yet.
    taken = {loc["time"] for loc in client.send("get_arrangement")["locators"]}
    target = next(t for t in (32.0, 36.0, 40.0, 44.0, 48.0, 52.0) if t not in taken)
    result = client.send_resolving_seek("create_locator", time=target, name="Chorus")
    assert result["locator"]["time"] == target, result
    assert result["locator"]["name"] == "Chorus", result
    try:
        client.send_resolving_seek("create_locator", time=target, name="Verse")
        raise AssertionError("collision was not refused")
    except CommandError as e:
        assert "already exists" in e.message, e
    return "created + rename verified; second create at same time refused"


@step("import_audio: generated sine WAV onto new audio track")
def check_import_audio(client):
    os.makedirs(SAMPLES_DIR, exist_ok=True)
    wav_path = os.path.join(SAMPLES_DIR, "checkpoint_tone_440.wav")
    write_sine_wav(Path(wav_path), 440.0, 0.37, 1.0)

    created = client.send("create_track", type="audio")
    state["audio_track"] = created["track_index"]
    result = client.send(
        "import_audio",
        track_index=state["audio_track"],
        file_path=wav_path,
        position=8.0,
    )
    # is_audio_clip is derivable and no longer emitted (2.5): audio = NOT midi.
    assert result["imported"]["is_midi_clip"] is False, result

    session_result = client.send(
        "import_audio",
        track_index=state["audio_track"],
        file_path=wav_path,
        slot_index=0,
    )
    assert session_result["imported"]["view"] == "session", session_result
    return f"440Hz tone: timeline at beat 8 AND session slot 0 (track {state['audio_track']})"


@step("envelope guards: arrangement + unwarped audio rejected, nothing destroyed")
def check_envelope_guards(client):
    # Live's own docstring: automation_envelope returns None for Arrangement
    # clips — the tool must refuse with a typed error BEFORE any clear.
    try:
        client.send(
            "set_clip_envelope",
            track_index=state["track"],
            arrangement_clip_index=0,
            mixer_parameter="volume",
            points=[{"time": 0.0, "value": 0.5}],
        )
        raise AssertionError("arrangement envelope write was accepted")
    except CommandError as e:
        assert e.error_type == "ValidationError", e
        assert "rrangement" in e.message, e

    # Unwarped audio: loop bounds in seconds, length undefined (LOM). Uses the
    # session audio clip from check_import_audio.
    toggled = client.send("set_clip", track_index=state["audio_track"], slot_index=0, warping=False)
    assert toggled["warping"] is False, toggled
    try:
        client.send(
            "set_clip_envelope",
            track_index=state["audio_track"],
            slot_index=0,
            mixer_parameter="volume",
            points=[{"time": 0.0, "value": 0.5}],
        )
        raise AssertionError("unwarped-audio envelope write was accepted")
    except CommandError as e:
        assert e.error_type == "ValidationError", e
        assert "arp" in e.message, e  # Warp/warp
    client.send("set_clip", track_index=state["audio_track"], slot_index=0, warping=True)

    # Bad point times must fail BEFORE the destructive clear: the LP sweep
    # written earlier must survive an invalid overwrite attempt.
    try:
        client.send(
            "set_clip_envelope",
            track_index=state["track"],
            slot_index=0,
            device_index=0,
            parameter="LP Freq",
            points=[{"time": 999.0, "value": 1.0}],
        )
        raise AssertionError("point beyond clip length was accepted")
    except CommandError as e:
        assert e.error_type == "ValidationError", e
    still_there = client.send(
        "get_clip_envelope",
        track_index=state["track"],
        slot_index=0,
        device_index=0,
        parameter="LP Freq",
    )
    assert still_there["exists"] is True, "guarded failure destroyed the envelope!"
    return "arrangement + unwarped + beyond-length all refused; existing envelope intact"


@step("device enabled: Device On toggled, is_active follows next request")
def check_device_enabled(client):
    result = client.send(
        "set_device_parameters", track_index=state["track"], device_index=0, enabled=False
    )
    assert result["device_on"]["value"] == 0.0, result
    # is_active is read-only and may read stale in the same task — the honest
    # readback is a SEPARATE request.
    listing = client.send("get_devices", track_index=state["track"])
    assert listing["devices"][0]["is_active"] is False, listing["devices"][0]
    client.send("set_device_parameters", track_index=state["track"], device_index=0, enabled=True)
    listing = client.send("get_devices", track_index=state["track"])
    assert listing["devices"][0]["is_active"] is True, listing["devices"][0]
    return "enabled=false/true drives Device On; is_active confirmed cross-request"


@step("insert_device: native Reverb by name, unknown name refused")
def check_insert_device(client):
    before = len(client.send("get_devices", track_index=state["track"])["devices"])
    result = client.send("insert_device", track_index=state["track"], device_name="Reverb")
    assert result["inserted"]["name"] == "Reverb", result
    assert result["device_count"] == before + 1, result
    client.send(
        "delete_device", track_index=state["track"], device_index=result["inserted"]["index"]
    )
    try:
        client.send(
            "insert_device", track_index=state["track"], device_name="Definitely Not A Device"
        )
        raise AssertionError("unknown device name was accepted")
    except CommandError as e:
        assert e.error_type == "LiveAPIError", e
    return "Reverb inserted+removed; unknown name -> LiveAPIError (VERIFY discharged)"


@step("parameter metadata: value_items + automation_state on a real device")
def check_param_metadata(client):
    device = client.send("get_devices", track_index=state["track"], device_index=0)["device"]
    by_name = {p["name"]: p for p in device["parameters"]}
    device_on = by_name.get("Device On")
    assert device_on is not None, list(by_name)[:10]
    assert device_on["is_quantized"] is True, device_on
    assert len(device_on.get("value_items", [])) >= 2, device_on
    # Since 2.5 these are ABSENT at their defaults (none / true).
    assert device_on.get("automation_state", "none") in ("none", "active", "overridden"), device_on
    assert isinstance(device_on.get("is_enabled", True), bool), device_on
    return f"Device On value_items={device_on['value_items']}"


@step("invalid scale name: typed error, batched tempo untouched")
def check_invalid_scale(client):
    tempo_before = client.send("get_transport_state")["tempo"]
    try:
        client.send("set_transport", tempo=tempo_before + 7, scale_name="Klingon Blues")
        raise AssertionError("invalid scale name was accepted")
    except CommandError as e:
        assert e.error_type == "LiveAPIError", e
        assert "rejected scale name" in e.message, e
    tempo_after = client.send("get_transport_state")["tempo"]
    assert abs(tempo_after - tempo_before) < 0.01, (
        f"tempo changed {tempo_before} -> {tempo_after} despite scale failure"
    )
    return "silent-no-op VERIFY discharged: readback turns it into a typed error, batch atomic"


@step("core meters: track + master move while a clip plays")
def check_meters(client):
    client.send("launch_clip", track_index=state["track"], slot_index=0)
    time.sleep(0.8)
    meters = client.send("get_track_meters")
    client.send("stop_clips", track_index=state["track"])
    # Match by INDEX, not name: a leftover 'MCP Test' track from an earlier
    # checkpoint run shares the name and reads 0.0 (found the hard way).
    test_track = next(t for t in meters["tracks"] if t["index"] == state["track"])
    master = meters["master_track"]
    assert test_track["output_meter_level"] > 0.0, test_track
    assert master["output_meter_level"] > 0.0, master
    return (
        f"MCP Test level={test_track['output_meter_level']:.2f}, "
        f"master level={master['output_meter_level']:.2f} (Live meter scale, not dB)"
    )


@step("play from beat 8: two-phase seek lands before playback starts")
def check_play_from_position(client):
    client.send("transport_control", action="stop")
    result = client.send_resolving_seek("transport_control", action="play", position=8.0)
    assert result.get("phase") != "seeking", "seeking never resolved"
    time.sleep(0.5)
    st = client.send("get_transport_state")
    client.send("transport_control", action="stop")
    assert st["is_playing"] is True, st
    assert 8.0 - 0.01 <= st["current_song_time"] <= 24.0, (
        f"playhead at {st['current_song_time']} — expected to be playing from beat 8"
    )
    return f"playing from {st['current_song_time']:.2f} (requested 8.0)"


@step("audible finale: play the ARRANGEMENT from the top for 5 seconds")
def check_finale(client):
    client.send("set_transport", back_to_arranger=False)
    # position=0.0 is a real seek by now — two-phase, so loop on 'seeking'.
    client.send_resolving_seek("transport_control", action="play", position=0.0)
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


@step("take lanes: create, write notes in lane clip, main-lane exclusion")
def check_take_lanes(client):
    before = client.send("get_arrangement", track_index=state["track"])["tracks"][0]
    assert "take_lanes" not in before, "fresh track already reports take_lanes"
    main_clip_count = len(before["arrangement_clips"])

    lane = client.send("create_take_lane", track_index=state["track"], name="MCP Take A")
    assert lane["take_lane_index"] == 0 and lane["name"] == "MCP Take A", lane

    created = client.send(
        "create_arrangement_clip",
        track_index=state["track"],
        start_time=24.0,
        length_beats=4.0,
        take_lane_index=0,
    )["created"]
    assert created["take_lane_index"] == 0, created

    client.send(
        "add_notes",
        track_index=state["track"],
        arrangement_clip_index=created["arrangement_clip_index"],
        take_lane_index=0,
        notes=[{"pitch": "C3", "start_time": 0.0, "duration": 1.0, "velocity": 90}],
    )
    read = client.send(
        "get_notes",
        track_index=state["track"],
        arrangement_clip_index=created["arrangement_clip_index"],
        take_lane_index=0,
    )
    assert read["note_count"] == 1 and read["notes"][0]["pitch"] == 60, read

    after = client.send("get_arrangement", track_index=state["track"])["tracks"][0]
    lanes = after.get("take_lanes")
    assert lanes and lanes[0]["name"] == "MCP Take A" and len(lanes[0]["clips"]) == 1, lanes
    # THE key VERIFY: the lane clip must not leak into the main-lane list.
    assert len(after["arrangement_clips"]) == main_clip_count, (
        "take-lane clip appeared in Track.arrangement_clips — main-lane "
        "exclusion assumption is WRONG, fix mock + docs"
    )
    return (
        "lane created+named, clip+note written via lane addressing, "
        "Track.arrangement_clips excludes lane clips (VERIFY discharged). "
        "Lane stays in the set — no delete API exists."
    )


@step("Simpler class properties + sample ops (class_name, modes, invoke)")
def check_simpler_class_props(client):
    # A .wav loaded onto the MIDI track lands as a Simpler WITH a sample —
    # class properties and sample ops need one to mean anything.
    client.send(
        "load_item",
        path=[
            "user_library",
            "Drum Kits",
            "CLOUD PluggNB Drum Kit (Producergrind)",
            "PG CLOUD 808 - Tremble - C.wav",
        ],
        track_index=state["track"],
    )
    device = client.send("get_devices", track_index=state["track"], device_index=0)["device"]
    assert device["class_name"] == "OriginalSimpler", (
        f"Simpler class_name is {device['class_name']!r} — fix _CLASS_PROPS key + mock"
    )
    props = device.get("class_properties")
    assert props, "no class_properties block for a real Simpler"
    assert props["playback_mode"]["items"] == ["Classic", "One-Shot", "Slicing"], props

    client.send(
        "set_device_parameters",
        track_index=state["track"],
        device_index=0,
        parameters=[
            {"parameter": "playback_mode", "value": "One-Shot"},
            {"parameter": "voices", "value": 4},
        ],
    )
    props = client.send("get_devices", track_index=state["track"], device_index=0)["device"][
        "class_properties"
    ]
    assert props["playback_mode"]["value"] == "One-Shot", props
    assert props["voices"] == 4, props

    # reverse twice = net no change to the loaded sample; length estimate real.
    invoked = client.send(
        "set_device_parameters",
        track_index=state["track"],
        device_index=0,
        invoke=[{"method": "reverse"}, {"method": "reverse"}, {"method": "guess_playback_length"}],
    )["invoked"]
    length = next(i.get("result") for i in invoked if i["method"] == "guess_playback_length")
    assert isinstance(length, (int, float)) and length > 0, invoked
    gates = {k: props.get(k) for k in ("can_warp_as", "can_warp_double", "can_warp_half")}
    return f"modes+voices round-trip, reverse x2 + length={length}; warp gates {gates}"


@step("EQ Eight class properties: global_mode Stereo->M/S->Stereo")
def check_eq8_class_props(client):
    inserted = client.send("insert_device", track_index=state["track"], device_name="EQ Eight")[
        "inserted"
    ]
    assert inserted["class_name"] == "Eq8", (
        f"EQ Eight class_name is {inserted['class_name']!r} — fix _CLASS_PROPS key + mock"
    )
    idx = inserted["index"]
    props = client.send("get_devices", track_index=state["track"], device_index=idx)["device"][
        "class_properties"
    ]
    assert props["global_mode"]["value"] == "Stereo", props
    client.send(
        "set_device_parameters",
        track_index=state["track"],
        device_index=idx,
        parameters=[
            {"parameter": "global_mode", "value": "M/S"},
            {"parameter": "oversample", "value": True},
        ],
    )
    props = client.send("get_devices", track_index=state["track"], device_index=idx)["device"][
        "class_properties"
    ]
    assert props["global_mode"]["value"] == "M/S" and props["oversample"] is True, props
    client.send("delete_device", track_index=state["track"], device_index=idx)
    return "global_mode + oversample round-trip; device removed"


@step("Drift indexed properties: runtime lists + label writes")
def check_drift_class_props(client):
    # Drift needs its OWN track: the MCP Test track already holds the Simpler,
    # and Live refuses a second instrument per chain (CONFIRMED on 12.4.3 —
    # "Device chains cannot have more than one instrument each", first 2.7
    # checkpoint run).
    drift_track = client.send("create_track", type="midi")["track_index"]
    try:
        inserted = client.send("insert_device", track_index=drift_track, device_name="Drift")[
            "inserted"
        ]
        idx = inserted["index"]
        props = client.send("get_devices", track_index=drift_track, device_index=idx)["device"][
            "class_properties"
        ]
        voice_modes = props["voice_mode"]["items"]
        sources = props["mod_matrix_lfo_source"]["items"]
        assert len(voice_modes) >= 2 and len(sources) >= 2, props
        assert isinstance(props["pitch_bend_range"], int), props

        # Write by label taken from Live's OWN list; the track is deleted
        # afterwards, so no restore write is needed.
        original = props["voice_mode"]["value"]
        target = next(m for m in voice_modes if m != original)
        client.send(
            "set_device_parameters",
            track_index=drift_track,
            device_index=idx,
            parameters=[{"parameter": "voice_mode", "value": target}],
        )
        readback = client.send("get_devices", track_index=drift_track, device_index=idx)["device"][
            "class_properties"
        ]["voice_mode"]["value"]
        assert readback == target, (readback, target)
    finally:
        client.send("delete_track", track_index=drift_track)
    # Echo the REAL vocabularies into the record for docs + mock backport.
    return f"voice_modes={voice_modes} mod_sources={sources[:8]}{'...' if len(sources) > 8 else ''}"


@step("master/return devices: Limiter on Main + return Reverb round trip")
def check_master_return_devices(client):
    # Return track A ships with a Reverb in the default set — read and tweak it.
    returns = client.send("get_devices", track_type="return", track_index=0)["devices"]
    assert returns, "return A reported no devices (expected the default Reverb)"
    client.send(
        "set_device_parameters",
        track_type="return",
        track_index=0,
        device_index=0,
        parameters=[{"parameter": 1, "value": 0.6}],
    )

    # The Main track: insert a native Limiter, confirm, remove (the workflow
    # that was impossible before 2.8 — the limiter-on-Main gap).
    inserted = client.send("insert_device", track_type="master", device_name="Limiter")["inserted"]
    devices = client.send("get_devices", track_type="master")["devices"]
    assert any(d["name"] == "Limiter" for d in devices), devices
    client.send(
        "set_device_parameters",
        track_type="master",
        device_index=inserted["index"],
        # Live 12's Limiter has no bare 'Gain' — the real names (first run's
        # error listed them): Input Gain, Ceiling, Release, Lookahead, ...
        parameters=[{"parameter": "Ceiling", "value": 0.5}],
    )
    client.send("delete_device", track_type="master", device_index=inserted["index"])

    # load_item route onto Main (what plug-ins like Ozone need): selection of
    # the master track is the VERIFY here. Native Limiter keeps it fast.
    loaded = client.send("load_item", path=["audio_effects", "Limiter"], track_type="master")
    assert loaded["onto_track"] in ("Main", "Master"), loaded
    devices = client.send("get_devices", track_type="master")["devices"]
    limiter_index = next(d["index"] for d in devices if d["name"] == "Limiter")
    client.send("delete_device", track_type="master", device_index=limiter_index)
    return (
        "return-A Reverb tweaked; Limiter inserted+set+removed on Main via "
        "insert_device AND load_item (master selection VERIFY discharged)"
    )


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


def main():
    # Windows consoles are often cp1252; never let an unencodable character
    # in a detail string crash a print.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    print("Checkpoint against real Ableton Live\n", flush=True)
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
        check_envelopes,
        check_locator,
        check_import_audio,
        check_envelope_guards,
        check_device_enabled,
        check_insert_device,
        check_param_metadata,
        check_invalid_scale,
        check_meters,
        check_play_from_position,
        check_take_lanes,
        check_simpler_class_props,
        check_eq8_class_props,
        check_drift_class_props,
        check_master_return_devices,
        check_finale,
        check_validation,
        check_live_error,
    ]
    # Cleanup runs REGARDLESS of the time budget: an aborted run must not
    # leave the user's set littered.
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
