# M4L Lab — AbletonMCP Tap

The tap gives the AI ears: a Max for Live audio effect that sits in a signal
chain (normally the Master track), measures loudness and an 8-band frequency
picture, and serves snapshots on **127.0.0.1:9878** using the same
length-prefixed JSON protocol as the main bridge. The MCP tool
`get_audio_levels` (lab branch) reads it; when the device isn't present the
tool answers `available: false` with a hint instead of erroring.

Requires Live Suite or Standard+M4L (the trial is Suite). The core bridge
never depends on this — it is an OPTIONAL extra by design.

## Building the device (one-time, ~5 minutes, needs Max = bundled with Suite)

This is the only supported route — do not try to "Save As .amxd" from a
standalone patcher window:

1. In Live's browser: **Max for Live → Max Audio Effect** — drag the *blank*
   one onto the **Master** track.
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
8. Back in Live: the device shows on the Master track. The Max Window
   (Right-click title bar → Open Max Window) should log
   `AbletonMCP Tap: serving on 127.0.0.1:9878`, and the status message box
   shows `SERVING 9878`.

## Reading it

```bash
cd C:/dev/ableton-mcp-m4l && .venv/Scripts/python.exe scripts/tap_checkpoint.py
```

or (lab MCP server) call the `get_audio_levels` tool, optionally with
`duration_seconds` (0–10) to sample a window instead of an instant.

## Honesty notes

- **Pre-fader**: device chains on the Master run BEFORE the master fader —
  readings ignore the fader position and can differ from Live's meter. The
  `clipping` flag is advisory, not mastering advice.
- `receiving_audio: false` after ~3 s of digital silence — also catches a
  bypassed device, DSP off, or a frozen track.
- Both listeners (9877 bridge, 9878 tap) are unauthenticated localhost
  services — same single-user trust model, loopback only.

## Troubleshooting

- Status `PORT BUSY` / Max Window shows bind failure → a second Tap device
  exists somewhere in the set (only one per set in v1); delete the duplicate.
  Duplicating/undoing the device can also cause a transient retry — it
  self-heals within ~5 s.
- Tool says `available: false` → is the device on a track, device power
  button on, and Live's audio engine running?
- Multi-tap (per-track ears, port-per-instance) is deliberately out of scope
  for v1.

## Promotion to main

Only after the live checkpoint passes, and as a purely additive merge:
`m4l/` + `mcp_server/m4l.py` + tool registration + tests; zero behavioral
changes to core files; README on main gains an "optional: audio tap
(Suite/M4L)" section. The core must keep working on Live Intro without it.
