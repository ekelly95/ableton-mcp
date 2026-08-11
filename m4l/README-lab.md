# M4L Lab — AbletonMCP Tap (v2)

The tap gives the AI ears: a Max for Live audio effect that sits in a signal
chain (normally the Main track — Live 12's name for the master), measures
stereo loudness and a 10-band
octave picture (31 Hz–16 kHz), and serves snapshots on **127.0.0.1:9878**
using the same length-prefixed JSON protocol as the main bridge. The MCP tool
`get_audio_levels` reads it; when the device isn't present the tool answers
`available: false` with a hint instead of erroring.

Requires Live Suite or Standard+M4L (the trial is Suite). The core bridge
never depends on this — it is an OPTIONAL extra by design (get_track_meters
gives coarse core-API metering without it).

## Upgrading from v1

The v2 patch changed the measurement protocol (stereo power messages instead
of the mono-sum values that cancelled on wide/anti-phase material), so a v1
device and the current server refuse to talk to each other — `get_audio_levels`
answers `available: false` with a rebuild hint rather than serving numbers
that look plausible and are wrong.

1. **Delete the old "AbletonMCP Tap" device from the Main track first**
   (two devices race for the port during a rebuild).
2. Follow the build steps below with the CURRENT `m4l\tap.maxpat`.
3. In step 6, copy the CURRENT `m4l\tap_server.js` — the patch and the script
   version together; mixing old and new is exactly what the protocol gate
   catches (`legacy_msgs` in ping counts old-protocol messages).
4. Save over the old `AbletonMCP Tap.amxd`.
5. Verify: `python scripts/tap_checkpoint.py` must report protocol v2,
   legacy_msgs 0, and pass its calibration steps.

## Building the device (one-time, ~10 minutes, needs Max = bundled with Suite)

This is the only supported route — do not try to "Save As .amxd" from a
standalone patcher window:

1. In Live's browser: **Max for Live → Max Audio Effect** — drag the *blank*
   one onto the **Main** track.
2. Click the device's **edit button** (pencil icon). Max opens.
3. In the Max patcher: **Ctrl+A** (select all) → **Delete** — yes, including
   the default plugin~/plugout~; our patch brings its own.
4. Open `m4l\tap.maxpat` in a TEXT editor (Notepad is fine), **Ctrl+A, Ctrl+C**
   (copy ALL the JSON).
5. Click once on the empty Max patcher, then **Ctrl+V**. The whole patch
   appears (Max instantiates pasted patcher JSON).
6. Copy `m4l\tap_server.js` into the same folder you will save the device to —
   OR (more robust) **File → Freeze Device**, which embeds the script.
7. **Ctrl+S** — save as `AbletonMCP Tap.amxd` in your User Library when asked.
8. Back in Live: the device shows on the Main track. The Max Window
   (Right-click title bar → Open Max Window) should log
   `AbletonMCP Tap: serving on 127.0.0.1:9878`, and the status message box
   shows `SERVING 9878`.

## Reading it

From the repo root:

```bash
.venv/Scripts/python.exe scripts/tap_checkpoint.py
```

or call the `get_audio_levels` tool, optionally with `duration_seconds`
(0–10) to sample a window instead of an instant.

## What v2 measures (and how honestly)

- **Stereo power, anti-phase safe.** Each channel is squared in MSP; the JS
  computes rms = sqrt(mean(L²+R²)/2). Wide or phase-inverted material reads
  its true loudness (v1 summed the raw waveforms first, so anti-phase content
  cancelled to −70 dB while the per-channel peaks sat at full level).
- **10 resonant octave bands, 31 Hz–16 kHz** (`fffb~ 10 31.25 2. 1.414`,
  Q ≈ 1.414 ≈ one-octave bandwidth, adjacent bands crossing near −3 dB).
  They are reson-style filters: they do not sum flat and the 16k band's upper
  skirt warps near Nyquist at 44.1 kHz. A meter, not an RTA.
- **~300 ms power averaging** (`average~ 14400` — the argument is SAMPLES:
  326 ms @ 44.1k, 300 ms @ 48k, 150 ms @ 96k). Always ≥ the 100 ms report
  cadence, so every moment of audio influences at least one reading (v1's
  1000-sample window was ~21 ms, leaving ~79 % of the audio unmeasured).
- **Clipping is latched**: sample-peak ≥ 0.999 in ANY ~100 ms frame within
  the 5 s window (not true-peak, no inter-sample detection). It self-clears
  as the window drains — including after the audio engine stops.
- **Staleness is explicit**: `stale: true` + `data_age_ms` when no
  measurement messages arrived for >2 s (device bypassed, DSP off); values
  are floored to −70 dB instead of freezing at their last reading.
- **Pre-fader**: device chains on the Main track run BEFORE the master fader —
  readings ignore the fader position and can differ from Live's meter. The
  `clipping` flag is advisory, not mastering advice.
- `receiving_audio: false` after ~3 s of digital silence — also catches a
  bypassed device, DSP off, or a frozen track (now backed by `stale`).
- Both listeners (9877 bridge, 9878 tap) are unauthenticated localhost
  services — same single-user trust model, loopback only.

## Version compatibility (any stale combination refuses, none mislead)

| device patch | tap_server.js | ping reports | get_audio_levels |
|---|---|---|---|
| v1 | v1 | protocol 1 | available:false + rebuild hint |
| v1 | v2 | protocol 2, legacy_msgs > 0 | available:false + rebuild hint |
| v2 | v1 | protocol 1 (v1 js ignores pow/bpow) | available:false + rebuild hint |
| v2 | v2 | protocol 2, legacy_msgs 0 | full v2 data |

## Build lessons (verified the hard way, 2026-08-11)

- Message boxes MUST contain raw `$1` — a backslash before it makes Max send
  the literal text "$1" instead of the number (the ping counters
  `msgs_ok/msgs_bad/last_bad_sample` exist to catch exactly this class).
- `tap_server.js` must sit NEXT TO the saved .amxd (or be frozen in — but
  freezing only embeds it if Max could FIND it at freeze time, so copy first).
- Exactly ONE tap device per set: a second instance loses the port race and
  serves nothing, while you keep talking to whichever one bound first —
  possibly on the wrong track.
- The device must be in an AUDIO path that actually carries the mix — the
  Main track. On an empty MIDI track it dutifully reports silence forever.

## Troubleshooting

- Status `PORT BUSY` / Max Window shows bind failure → a second Tap device
  exists somewhere in the set (only one per set is supported); delete the
  duplicate.
  Duplicating/undoing the device can also cause a transient retry — it
  self-heals within ~5 s.
- Tool says `available: false` → is the device on a track, device power
  button on, and Live's audio engine running? If the hint mentions
  rebuilding: the device predates the current protocol — see "Upgrading
  from v1".
- `ping` shows `legacy_msgs` climbing → the PATCH inside the device is v1
  while the js is v2; rebuild from the current tap.maxpat.
- Multi-tap (per-track ears, port-per-instance) is deliberately out of scope
  for now.
