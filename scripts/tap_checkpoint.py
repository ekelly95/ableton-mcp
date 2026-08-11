"""L4: verify the AbletonMCP Tap device end-to-end against running Live.

Needs: Live open with the bridge enabled, the Tap device built (m4l/README-lab.md)
and sitting on the Master track, and the 'MCP Test' track from the main
checkpoint (or any playable clip on track 4).

Run:  python scripts/tap_checkpoint.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.client import AbletonClient  # noqa: E402
from mcp_server.m4l import TapClient, TapUnavailable, get_audio_levels  # noqa: E402


def main() -> None:
    tap = TapClient()

    print("1. Tap ping...", flush=True)
    try:
        info = tap.send("ping")
    except TapUnavailable as e:
        print(f"   FAIL: tap unreachable ({e})")
        print("   Build/enable the device first — m4l/README-lab.md, then retry.")
        sys.exit(1)
    print(
        f"   OK: {info['name']}, protocol v{info['tap_protocol_version']}, up {info['uptime_seconds']}s"
    )

    print("2. Silence heuristic (nothing should be playing)...", flush=True)
    quiet = get_audio_levels(tap)
    print(f"   receiving_audio={quiet['receiving_audio']} rms={quiet['rms_db']} dB")
    if quiet["receiving_audio"]:
        print("   NOTE: something is already playing/feeding audio — silence check skipped")

    print("3. Launching a clip and sampling 3s through the tap...", flush=True)
    bridge = AbletonClient(timeout=15)
    try:
        overview = bridge.send("get_session_overview")
        target = next(
            (
                (t["index"], s["slot_index"])
                for t in overview["tracks"]
                for s in t.get("clip_slots", [])
                if s.get("has_clip") and t.get("devices")
            ),
            None,
        )
        if target is None:
            print(
                "   FAIL: no clip on a track with an instrument. Run scripts/live_checkpoint.py first."
            )
            sys.exit(1)
        track_index, slot_index = target
        bridge.send("launch_clip", track_index=track_index, slot_index=slot_index)
        time.sleep(0.5)
        loud = get_audio_levels(tap, duration_seconds=3)
        bridge.send("stop_clips", track_index=track_index)
        bridge.send("transport_control", action="stop")
    finally:
        bridge.close()

    print(f"   receiving_audio={loud['receiving_audio']}")
    print(
        f"   rms_max={loud['rms_db_max']} dB  peak_max={loud['peak_db_max']} dB  clipping={loud['clipping']}"
    )
    for band in loud["bands_max"]:
        bar = "#" * max(0, int((band["level_db"] + 70) / 3))
        print(f"   {band['hz']:>5} Hz | {band['level_db']:>7.1f} dB | {bar}")

    ok = loud["receiving_audio"] and loud["rms_db_max"] > -60.0
    if not ok:
        print("\nFAIL: tap saw no signal while the clip played.")
        print("Checks: device on the MASTER track? powered on? Max Window says SERVING?")
        sys.exit(1)

    print("\nPASS: the AI can hear the set. (Readings are pre-master-fader.)")


if __name__ == "__main__":
    main()
