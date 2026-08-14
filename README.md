# ableton-mcp

[![Tests](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml)

AI agents control Ableton Live. Describe what you want — a drum groove, a
chord progression, a filter sweep, an arrangement — and your assistant builds
it in your session while you watch. Works with any MCP client over stdio:
Claude Desktop, Claude Code, Codex, anything.

Focus: **stable MIDI readback and precise edits**, **clip automation
envelopes**, **third-party VST/VST3 loading**, and **audio-level feedback**
(per-track meters plus an optional calibrated loudness/spectrum meter). Core
needs no Max for Live. MIT licensed. Every verified fact is from Live 12.4.3
(Trial); other editions should work but are untested. Limits: one MCP client
at a time; installing requires a local checkout.

**Status: experimental, verified on both platforms.** Windows 11 end-to-end;
macOS via the full 42-step live checkpoint (Apple Silicon Mac mini, macOS
Tahoe 26.6.1, 2026-08-12) over its Unix-socket transport — both on Live
12.4.3.

## What the AI can do

46 tools:

- Read the whole session at a glance.
- Create/rename/mix tracks; create, duplicate, launch, and edit clips.
- Write MIDI by note name ("C3", "F#4" — Ableton convention, C3=60) with
  per-note chance and velocity spread; edit single notes by ID without
  rewriting the clip.
- Draw automation envelopes on session clips — any device knob or the track
  mixer (arrangement clips can't hold clip automation; a Live API limit).
- Set the song's key and scale.
- Build on the Arrangement timeline: create MIDI clips directly on it, stamp
  session loops onto it, drop named locators, edit timeline clips.
- Import audio into the session or timeline — the landing pad for sample
  generation: write a file, call `import_audio`.
- Browse the library and load instruments/effects — including third-party
  VST/VST3 plug-ins (2.4) — or insert native devices by name.
- Search Live's own library database offline (samples, presets, MIDI,
  grooves, plug-ins by name/tag/kind, ranked by how often you use them) and
  rank sounds by **sonic similarity** using Live's own audio analysis (2.6).
- Turn any device knob with its human-readable choices visible.
- Read every track's output meters (core API — is it making sound, how
  loud?); control tempo, loop, metronome, playback, and (guarded)
  arrangement recording.
- Inspect calibrated audio levels with the optional device below.

## Optional audio metering (needs Max for Live)

`get_audio_levels` reads the "AbletonMCP Tap" device on the Main track
(Suite, or Standard with the M4L add-on): stereo loudness and peaks in dBFS
(anti-phase safe), a latched clipping flag, and a 10-band octave view
(31 Hz–16 kHz). Measurements, not streamed audio. Setup:
[m4l/README-lab.md](m4l/README-lab.md). Without the device it answers
`available: false`; `get_track_meters` still gives coarse levels.

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

Two halves, one source of truth: every tool is generated from the command
registry the Live-side script executes — nothing is defined twice, and a
schema-hash handshake warns if the halves drift. Design decisions and
verified Live API facts: [docs/architecture.md](docs/architecture.md). The
playbook an AI agent should read before driving a session:
[AGENTS.md](AGENTS.md).

## Setup

Windows or macOS (both hardware-verified — see Status), Python 3.11+,
[uv](https://docs.astral.sh/uv/), Ableton Live 12 (verified on 12.4.3; core
tools read scale properties Live 12 added, so Live 11 won't work;
`insert_device` needs 12.3+).

```bash
git clone https://github.com/ekelly95/ableton-mcp.git && cd ableton-mcp
uv venv
uv pip install -e .
.venv/Scripts/python.exe scripts/install_control_surface.py
```

On macOS, read `.venv/Scripts/...exe` as `.venv/bin/...` throughout.

One-time, in Live: **Options → Preferences → Link, Tempo & MIDI**, set a
free Control Surface dropdown to **AbletonMCP** (Input/Output: None), restart
Live. After any update: re-run the install script, restart Live.

Register with your MCP client — e.g. Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "ableton": {
      "command": "C:\\path\\to\\ableton-mcp\\.venv\\Scripts\\ableton-mcp.exe",
      "args": []
    }
  }
}
```

Or Codex (`~/.codex/config.toml`) — single quotes keep Windows backslashes
literal, and Codex's Windows launcher wants the real `.exe`, not a shim:

```toml
[mcp_servers.ableton]
command = 'C:\path\to\ableton-mcp\.venv\Scripts\ableton-mcp.exe'
startup_timeout_sec = 30
```

Run one client at a time — the bridge serves a single serial connection.
`scripts/toggle_desktop_client.py on|off` flips Claude Desktop's
registration, so switching with Codex is one command.

## Health check

Ask the AI to run `get_bridge_status`, or:

```bash
.venv/Scripts/python.exe scripts/smoke_test.py
```

"Cannot connect" almost always means Live isn't running or the control
surface isn't enabled in Preferences.

## Development

```bash
uv pip install -e ".[dev]"
.venv/Scripts/python.exe -m pytest
```

Tests run without Live — `tests/mock_live.py` encodes real-Live behaviour
(verified on 12.4.3) with provenance comments. `scripts/live_checkpoint.py`
re-verifies the full surface against a running Live and leaves an audible
"MCP Test" track. One checkpoint step loads a specific drum sample from the
author's User Library — edit that path on another machine or it fails late
(see the roadmap item on making the checkpoint self-contained). Generated
samples go in `samples/` (override with `ABLETON_MCP_SAMPLES_DIR`).

## Support expectations

Shared as-is: a personal tool I use for my own music. Issues and PRs are
welcome and read, but there's no promised response time and no roadmap
commitment. Fork freely — it's MIT.

## License

MIT — see [LICENSE](LICENSE).
