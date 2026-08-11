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
                print(f"  {PASS}  {name}" + (f" — {detail}" if detail else ""))
            except Exception as e:
                results.append((FAIL, name, str(e)))
                print(f"  {FAIL}  {name} — {type(e).__name__}: {e}")
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
    assert result["pong"] and result["version"] == "2.0.0", result
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


@step("remove_notes by region, re-add")
def check_remove_notes(client):
    result = client.send(
        "remove_notes",
        track_index=state["track"],
        slot_index=0,
        from_time=3.0,
        time_span=10.0,
    )
    assert result["note_count"] == 3, result
    client.send(
        "add_notes", track_index=state["track"], slot_index=0, notes=[NOTES[3]]
    )
    return None


@step("duplicate_clip into next slot")
def check_duplicate_clip(client):
    result = client.send("duplicate_clip", track_index=state["track"], slot_index=0)
    return f"landed in slot {result['new_slot']} (VERIFY: next-slot assumption)"


@step("launch_clip + stop_clips")
def check_launch(client):
    client.send("launch_clip", track_index=state["track"], slot_index=0)
    time.sleep(2.5)
    clips = client.send("get_clips", track_index=state["track"])
    slot = clips["tracks"][0]["clip_slots"][0]
    playing = slot["is_playing"] or slot["is_triggered"]
    client.send("stop_clips", track_index=state["track"])
    assert playing, f"clip did not report playing/triggered: {slot}"
    return "clip fired (triggered/playing state confirmed)"


@step("transport: tempo set + play + stop + restore")
def check_transport(client):
    client.send("set_transport", tempo=100.0)
    st = client.send("get_transport_state")
    assert st["tempo"] == 100.0, st
    client.send("transport_control", action="play")
    time.sleep(1.0)
    st = client.send("get_transport_state")
    playing = st["is_playing"]
    client.send("transport_control", action="stop")
    client.send("set_transport", tempo=state["original_tempo"])
    assert playing, "transport did not report playing"
    return f"tempo restored to {state['original_tempo']}"


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
    print("P4 checkpoint against real Ableton Live\n")
    client = AbletonClient()
    for check in [
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
        check_validation,
        check_live_error,
    ]:
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
