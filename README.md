# ableton-mcp

[![Tests](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml)

AI agents control Ableton Live. Describe what you want — a drum groove, a
chord progression, a filter sweep, an arrangement — and your assistant builds
it in your session while you watch. Works with any MCP client over stdio:
Claude Desktop, Claude Code, Codex, anything.

Focus: **stable MIDI readback and precise edits**, **clip automation
envelopes**, **third-party VST/VST3 loading**, and **audio-level feedback**
(per-track meters plus an optional calibrated loudness/spectrum meter). Core
needs no Max for Live. MIT licensed. Verified on Live 12.4.3 and 12.4.6
(Trial editions); other editions should work but are untested.

**Status: experimental, verified on both platforms.** Windows 11 end-to-end
on Live 12.4.3, and again on 12.4.6 (October 2026: every 2.9-2.20 feature,
plus a re-check of the earlier claims); macOS via the full 42-step live
checkpoint on 12.4.3 (Apple Silicon Mac mini, macOS Tahoe 26.6.1,
2026-08-12) over its Unix-socket transport. What is and isn't verified, per
claim: `AGENTS.md` and `docs/architecture.md`.

## What the AI can do

50 tools:

- Read the whole session at a glance; look at any other object in Live's API
  by its path with the read-only `inspect`.
- Create, rename and mix tracks; create, duplicate, launch, and edit clips
  and scenes.
- Write MIDI by note name ("C3", "F#4" — Ableton convention, C3=60) with
  per-note chance and velocity spread, or in a compact bar|beat notation;
  edit single notes by ID without rewriting the clip; reshape a clip's notes
  (swing, quantize, ramps, ratchets) without reading them first.
- Draw automation envelopes on session clips — any device knob or the track
  mixer (arrangement clips can't hold clip automation; a Live API limit).
- Build on the Arrangement timeline: create MIDI clips directly on it or in
  take lanes, stamp session loops onto it, drop named locators, edit timeline
  clips.
- Import audio into the session or timeline — the landing pad for sample
  generation: write a file, call `import_audio`.
- Browse the library and load instruments/effects — including third-party
  VST/VST3 plug-ins (choosing a patch inside a plug-in is still a manual
  step) — or insert native devices by name.
- Search Live's own library database offline (samples, presets, MIDI,
  grooves, plug-ins by name/tag/kind, ranked by how often you use them) and
  rank sounds by **sonic similarity** using Live's own audio analysis.
- Turn any device knob — on regular, return and Main tracks — by a 0-1 value
  or by what Live displays ("-6 dB", "800 Hz", "High-pass").
- Work inside racks, including Drum Racks and the nested racks of Live's Core
  Library kits: add and remove devices on a pad, name, mute, solo and mix
  pads (`set_chain`), and put a sample on a pad (`load_sample`).
- Undo and redo: each write is one step in Live's undo history.
- Measure the mix without Max for Live: `capture_audio` records Live's output
  and reports loudness (LUFS), true peak, an octave-band spectrum and stereo
  width; `analyze_audio` does the same for any WAV or AIFF file.
- Control tempo, key and scale, loop, metronome, playback, and (guarded)
  arrangement recording; read every track's output meters (is it making
  sound, how loud?).
- Install or update its own Live-side script (`install_control_surface`).

## Optional audio metering (needs Max for Live)

`get_audio_levels` reads the "AbletonMCP Tap" device on the Main track
(Suite, or Standard with the M4L add-on): stereo loudness and peaks in dBFS,
a latched clipping flag, and a 10-band octave view — measurements, not
streamed audio. Setup: [m4l/README-lab.md](m4l/README-lab.md).
Without the device it answers `available: false`.

## How it works

```
AI client ── MCP (stdio) ── mcp_server ── local socket ── control surface inside Live
```

The local socket is TCP `127.0.0.1:9877` on Windows, a Unix socket
(`/tmp/ableton_mcp.sock`) on macOS.

**Worth knowing before you enable it:** that socket has no password. It is
loopback-only, so nothing off your machine can reach it — but while Live is
running with the control surface enabled, any program on the machine can drive
your session through it, deletes included. This is a single-user tool by
design; the reasoning, and what is done to keep the boundary tight, is in
[docs/architecture.md](docs/architecture.md#trust-model).

Every tool is generated from the command registry the Live-side script
executes, so nothing is defined twice, and a version and schema handshake
warns if the two halves drift apart. Design decisions and verified Live API
facts: [docs/architecture.md](docs/architecture.md). The playbook an AI agent
should read before driving a session: [AGENTS.md](AGENTS.md).

## Setup

Windows or macOS, Ableton Live 12 (verified on 12.4.3 and 12.4.6; core tools
read scale properties Live 12 added, so Live 11 won't work; `insert_device`
needs 12.3+).

**1. Install the MCP server** — pick one:

- **Claude Desktop, one click:** download `ableton-mcp-<version>.mcpb` from
  the latest release and open it (or drag it into Settings → Extensions).
  It's built to the MCP Bundle spec (the host installs the dependencies, so
  no Python is needed) but hasn't yet been tested in Claude Desktop itself —
  please open an issue if it doesn't install.
- **Any MCP client, via [uv](https://docs.astral.sh/uv/):** register the
  command `uvx --python 3.13 --from git+https://github.com/ekelly95/ableton-mcp ableton-mcp`
  (Codex: `command = 'uvx'`, `args = ['--python', '3.13', '--from', 'git+https://github.com/ekelly95/ableton-mcp', 'ableton-mcp']`).
  `--python 3.13` matters: without it uv may pick a Python so new that a
  dependency has no prebuilt wheel yet and needs compilers to install.
- **From a checkout (development):**
  ```bash
  git clone https://github.com/ekelly95/ableton-mcp.git && cd ableton-mcp
  uv sync
  ```
  and register `.venv/Scripts/ableton-mcp.exe` (macOS: `.venv/bin/ableton-mcp`).

**2. Put the Live side in place:** ask the model to run
`install_control_surface` (or run `ableton-mcp-install`). It copies the
control surface into Live's User Library and, on later updates, reloads it in
a running Live when it can.

**3. One time, in Live:** restart Live, then **Options → Preferences → Link,
Tempo & MIDI**, set a free Control Surface dropdown to **AbletonMCP**
(Input/Output: None).

Several clients (Claude Desktop, Claude Code, Codex) can stay connected at
once. Their commands run in Live one at a time, and a write is refused if the
set's tracks, devices, clips or scenes changed since that client last read
them.

## Health check

Ask the AI to run `get_bridge_status`. From a checkout,
`uv run python scripts/smoke_test.py` checks the connection from a terminal.

"Cannot connect" almost always means Live isn't running or the control
surface isn't enabled in Preferences.

## Development

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run ableton-mcp-install
uv run python packaging/build_mcpb.py     # dist/ableton-mcp-<version>.mcpb
```

`ableton-mcp-install` pushes handler changes (`control_surface/commands`,
`utils`) into a running Live without a restart (`--no-reload` skips that); it
tells you when a change needs one.

Tests run without Live — `tests/mock_live.py` encodes real-Live behaviour
(verified on 12.4.3) with provenance comments. `scripts/live_checkpoint.py`
re-verifies the full surface against a running Live and leaves an audible
"MCP Test" track. One checkpoint step loads a specific drum sample from the
author's User Library — edit that path on another machine or it fails late.
Generated samples go in `samples/` (override with `ABLETON_MCP_SAMPLES_DIR`).
Release history and remaining work: [docs/roadmap.md](docs/roadmap.md).

### Live on another machine (e.g. a Windows VM)

Live's listeners stay loopback-only, so reach them through an SSH tunnel and
point the MCP server at it:

```sh
ssh -N -L 9877:127.0.0.1:9877 -L 9878:127.0.0.1:9878 <vm>
ABLETON_MCP_HOST=127.0.0.1 ableton-mcp
```

`ABLETON_MCP_HOST` forces TCP (otherwise macOS/Linux use the Unix socket);
`ABLETON_MCP_PORT`, `ABLETON_TAP_HOST` and `ABLETON_TAP_PORT` override the
rest. These only change where the MCP server connects, never where Live binds.

## Support expectations

Shared as-is: a personal tool I use for my own music. Issues and PRs are
welcome and read, but there's no promised response time and no roadmap
commitment. Fork freely — it's MIT.

## License

MIT — see [LICENSE](LICENSE).
