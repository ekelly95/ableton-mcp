"""Verify the AbletonMCP Tap device (v2) end-to-end against running Live.

Needs: Live open with the bridge enabled, the Tap v2 device built
(m4l/README-lab.md) and sitting on the Master track, and the 'MCP Test' track
from the main checkpoint (or any playable clip on a track with an instrument).

Steps: protocol gate, silence heuristic, clip sampling, then two calibration
checks with generated tones — a mono 1 kHz sine at known level (band mapping +
level accuracy, loose tolerances: this is a meter, not a lab instrument), and
an anti-phase stereo tone (the v1 mono-sum bug read −70 dB on it; v2 stereo
power metering must not).

Run:  python scripts/tap_checkpoint.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wav_util import write_sine_wav  # noqa: E402

from control_surface.config import SAMPLES_DIR  # noqa: E402
from mcp_server.client import AbletonClient  # noqa: E402
from mcp_server.m4l import (  # noqa: E402
    EXPECTED_TAP_PROTOCOL,
    TapClient,
    TapUnavailable,
    get_audio_levels,
)

# Calibration tone level, one derivation for every tolerance below:
# amplitude 0.25 of full scale → peak = 20*log10(0.25) ≈ −12 dBFS, and a
# sine's RMS sits 3 dB below its peak → ≈ −15 dB RMS.
CAL_AMPLITUDE = 0.25
CAL_PEAK_DB = -12.0
CAL_RMS_DB = -15.0

_phase_counter = 0


def phase(title: str) -> None:
    global _phase_counter
    _phase_counter += 1
    print(f"{_phase_counter}. {title}", flush=True)


def sample_tone(bridge: AbletonClient, tap: TapClient, wav_path: Path, seconds: float = 3):
    """Import a WAV onto a fresh audio track's slot 0, play it, sample, clean up."""
    created = bridge.send("create_track", type="audio")
    track_index = created["track_index"]
    try:
        bridge.send("import_audio", track_index=track_index, file_path=str(wav_path), slot_index=0)
        bridge.send("launch_clip", track_index=track_index, slot_index=0)
        time.sleep(0.5)
        levels = get_audio_levels(tap, duration_seconds=seconds)
        bridge.send("stop_clips", track_index=track_index)
        return levels
    finally:
        try:
            bridge.send("delete_track", track_index=track_index)
        except Exception as e:
            print(f"   (cleanup: could not delete calibration track {track_index}: {e})")


def print_bands(levels) -> None:
    for band in levels["bands_max"]:
        bar = "#" * max(0, int((band["level_db"] + 70) / 3))
        print(f"   {band['hz']:>4} Hz | {band['level_db']:>7.1f} dB | {bar}")


def main() -> None:
    # Windows consoles are often cp1252 — never let '≈'/'≥' in a print crash
    # the run (same guard as live_checkpoint.py).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    tap = TapClient()
    failures: list[str] = []

    phase("Tap ping + protocol gate...")
    try:
        info = tap.send("ping")
    except TapUnavailable as e:
        print(f"   FAIL: tap unreachable ({e})")
        print("   Build/enable the device first — m4l/README-lab.md, then retry.")
        sys.exit(1)
    version = info.get("tap_protocol_version")
    legacy = info.get("legacy_msgs", 0)
    print(
        f"   {info['name']}: protocol v{version}, bands={info.get('bands')}, "
        f"legacy_msgs={legacy}, up {info['uptime_seconds']}s"
    )
    if version != EXPECTED_TAP_PROTOCOL or legacy > 0:
        print(
            f"   FAIL: device speaks v{version} with legacy_msgs={legacy} "
            f"(need v{EXPECTED_TAP_PROTOCOL}, legacy 0)."
        )
        print("   Rebuild the device from the CURRENT m4l/tap.maxpat + tap_server.js")
        print("   (m4l/README-lab.md, 'Upgrading from v1'), then retry.")
        sys.exit(1)

    phase("Silence heuristic (nothing should be playing)...")
    quiet = get_audio_levels(tap)
    print(
        f"   receiving_audio={quiet['receiving_audio']} stale={quiet.get('stale')} "
        f"rms={quiet['rms_db']} dB"
    )
    if quiet.get("stale"):
        print("   FAIL: tap is STALE — is the device powered on and the audio engine running?")
        sys.exit(1)
    if quiet["receiving_audio"]:
        print("   NOTE: something is already playing/feeding audio — silence check skipped")

    phase("Launching a clip and sampling 3s through the tap...")
    bridge = AbletonClient(timeout=30)
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

        print(f"   receiving_audio={loud['receiving_audio']} clipping={loud['clipping']}")
        print(f"   rms_max={loud['rms_db_max']} dB  peak_max={loud['peak_db_max']} dB")
        print_bands(loud)
        if not (loud["receiving_audio"] and loud["rms_db_max"] > -60.0):
            print("\nFAIL: tap saw no signal while the clip played.")
            print("Checks: device on the MASTER track? powered on? Max Window says SERVING?")
            sys.exit(1)

        phase(
            f"Calibration: mono 1 kHz sine, peak {CAL_PEAK_DB} dBFS (expected rms ≈ {CAL_RMS_DB})..."
        )
        samples_dir = Path(SAMPLES_DIR)
        samples_dir.mkdir(parents=True, exist_ok=True)
        cal_wav = samples_dir / "tap_cal_1k.wav"
        write_sine_wav(cal_wav, 1000.0, CAL_AMPLITUDE, 4.0)
        cal = sample_tone(bridge, tap, cal_wav)
        print(f"   rms_max={cal['rms_db_max']} dB  peak_max={cal['peak_db_max']} dB")
        print_bands(cal)
        by_hz = {b["hz"]: b["level_db"] for b in cal["bands_max"]}
        hottest = max(cal["bands_max"], key=lambda b: b["level_db"])
        if hottest["hz"] != "1k":
            failures.append(f"calibration: hottest band was {hottest['hz']}, expected 1k")
        if abs(by_hz["1k"] - CAL_RMS_DB) > 6.0:
            failures.append(f"calibration: 1k band {by_hz['1k']} dB not within ±6 of {CAL_RMS_DB}")
        if abs(cal["rms_db_max"] - CAL_RMS_DB) > 3.0:
            failures.append(
                f"calibration: rms {cal['rms_db_max']} dB not within ±3 of {CAL_RMS_DB}"
            )
        if abs(cal["peak_db_max"] - CAL_PEAK_DB) > 2.0:
            failures.append(
                f"calibration: peak {cal['peak_db_max']} dB not within ±2 of {CAL_PEAK_DB}"
            )
        # Low bands: strict. High bands: the octave filters are deliberately
        # broad (2-pole skirts, Q≈1.414 for contiguous coverage) and the 16k
        # response warps near Nyquist at 44.1k (documented in README-lab.md) —
        # measured on real Live: 8k ≈ −8 dB, 16k ≈ −6 dB below a 1 kHz tone.
        for far, min_below in (("31", 8.0), ("63", 8.0), ("125", 8.0), ("8k", 4.0), ("16k", 4.0)):
            if by_hz["1k"] - by_hz[far] < min_below:
                failures.append(
                    f"calibration: band {far} ({by_hz[far]} dB) not >={min_below} dB "
                    f"below 1k ({by_hz['1k']})"
                )

        phase("Anti-phase stereo 220 Hz (v1 mono-sum read -70 dB here)...")
        anti_wav = samples_dir / "tap_cal_antiphase.wav"
        write_sine_wav(anti_wav, 220.0, CAL_AMPLITUDE, 4.0, antiphase=True)
        anti = sample_tone(bridge, tap, anti_wav)
        print(f"   rms_max={anti['rms_db_max']} dB  peak_max={anti['peak_db_max']} dB")
        if anti["rms_db_max"] <= -30.0:
            failures.append(
                f"anti-phase: rms {anti['rms_db_max']} dB — stereo power metering "
                f"should read ≈ {CAL_RMS_DB}, the v1 mono-sum bug reads -70"
            )
        if abs(anti["peak_db_max"] - CAL_PEAK_DB) > 2.0:
            failures.append(
                f"anti-phase: peak {anti['peak_db_max']} dB not within ±2 of {CAL_PEAK_DB}"
            )

        bridge.send("transport_control", action="stop")
    finally:
        bridge.close()

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f" - {f}")
        sys.exit(1)

    print(
        f"\nPASS {_phase_counter}/{_phase_counter}: the AI can hear the set, in tune with reality."
    )
    print("(Readings are pre-master-fader; bands are resonant octave filters.)")


if __name__ == "__main__":
    main()
