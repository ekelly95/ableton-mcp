# ableton-mcp

[![Tests](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml)

AI agents control Ableton Live. You describe what you want — a drum groove, a
chord progression, a filter sweep, a song arrangement — and your AI assistant
builds it in your Live session while you watch. Works with any MCP client:
Claude Desktop, Claude Code, Codex, or anything else that speaks the Model
Context Protocol over stdio.

The bridge focuses on **stable MIDI readback and precise edits**, **clip
automation envelopes**, **loading third-party VST/VST3 plug-ins**, and
**audio-level feedback** through per-track meters and an optional calibrated
loudness/spectrum meter. The core bridge needs no Max for Live, and it is MIT
licensed. Every verified fact comes from Live 12.4.3 (Trial); other editions
should work but are untested. Current limitations: it serves one MCP client at
a time, and installation requires a local checkout.

**Status: experimental, verified on both platforms.** Windows: end-to-end
against Ableton Live 12.4.3 on Windows 11. macOS: full 42-step live checkpoint
passed against Live 12.4.3 (Trial) on an Apple Silicon Mac mini running macOS
Tahoe 26.6.1 (2026-08-12), over the Unix-socket transport the Mac build uses.

## What the AI can do with it

46 tools: read the whole session at a glance; create/rename/mix tracks;
create, duplicate, launch and edit clips; write MIDI notes by name ("C3",
"F#4" — Ableton convention, C3=60) with per-note chance and velocity spread;
edit single notes precisely by ID without rewriting the clip; **draw
automation envelopes on session clips** (filter sweeps, volume fades — any
device knob or the track mixer; arrangement clips can't hold clip automation,
a Live API limit); set the song's key and scale; build songs on the
Arrangement timeline (create MIDI clips directly on it, stamp session loops
onto it, drop named locators, edit timeline clips); import audio files into
the session or onto the timeline (the landing pad for sample generation —
generators just write a file and call import_audio); browse Live's library
and load instruments/effects — including third-party VST/VST3 plug-ins
(2.4) — or insert native devices directly by name;
search Live's own library database offline — samples, presets, MIDI, grooves,
plug-ins by name/tag/kind, ranked by how often you use them — and rank sounds
by **sonic similarity** to a reference using Live's own audio analysis (2.6);
turn any device knob with its human-readable choices visible; read every
track's output meters (core API — is it making sound, how loud?); control
tempo, loop, metronome, playback and (via its own guarded tool) arrangement
recording; and inspect calibrated audio levels with the optional device below.

## Optional audio metering (needs Max for Live)

The `get_audio_levels` tool reports stereo loudness and peaks in dBFS
(anti-phase safe), a latched clipping flag, and a 10-band octave view
(31 Hz–16 kHz) from the "AbletonMCP Tap" Max for Live device on the Main
track — so Suite, or Standard with the Max for Live add-on. It provides
measurements rather than streamed audio. Setup instructions:
[m4l/README-lab.md](m4l/README-lab.md). Without the device, the tool reports
`available: false`; `get_track_meters` still provides coarse level checks.

## How it works

```
AI client ── MCP (stdio) ── mcp_server ── local socket ── control surface inside Live
```

The local socket is TCP `127.0.0.1:9877` on Windows and a Unix socket
(`/tmp/ableton_mcp.sock`) on macOS.

Two halves, one source of truth: every tool is generated from the command
registry that the Live-side script executes — nothing is defined twice, and a
schema-hash handshake warns if the two ever drift apart. See
[docs/architecture.md](docs/architecture.md) for design decisions and
verified Live API facts, and [AGENTS.md](AGENTS.md) for the operating
playbook an AI agent should read before driving a session.

## Setup

Requirements: Windows or macOS (both hardware-verified — see Status),
Python 3.11+, [uv](https://docs.astral.sh/uv/), Ableton Live 12 (verified on
12.4.3; the transport and session-overview tools read scale properties Live 12
added, so Live 11 is not supported, and `insert_device` needs 12.3+).

```bash
git clone https://github.com/ekelly95/ableton-mcp.git && cd ableton-mcp
uv venv
uv pip install -e .
.venv/Scripts/python.exe scripts/install_control_surface.py
```

On macOS, the venv paths are `.venv/bin/python` and `.venv/bin/ableton-mcp`
wherever this README says `.venv/Scripts/...exe`.

Then, one-time, in Ableton Live: **Options → Preferences → Link, Tempo &
MIDI**, set a free Control Surface dropdown to **AbletonMCP** (Input/Output:
None), and restart Live. Re-run the install script + restart Live after any
update.

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

Or Codex (`~/.codex/config.toml`) — single-quoted so Windows backslashes are
taken literally, and pointing at the real `.exe` rather than a shim, which is
what Codex's Windows process launcher wants:

```toml
[mcp_servers.ableton]
command = 'C:\path\to\ableton-mcp\.venv\Scripts\ableton-mcp.exe'
startup_timeout_sec = 30
```

Run one MCP client against it at a time; the bridge processes a single serial
connection. `scripts/toggle_desktop_client.py on|off` changes Claude Desktop's
registration so switching between Desktop and Codex is one command.

## Health check

Ask the AI to run `get_bridge_status`, or from a terminal:

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

The test suite runs without Live — a mock stands in (`tests/mock_live.py`),
encoding real-Live behaviour verified on 12.4.3 with provenance comments.
`scripts/live_checkpoint.py` re-verifies the full surface against a running
Live and leaves an audible "MCP Test" track behind. One checkpoint step loads
a specific drum sample from the author's User Library, so on another machine
it fails late unless that path is edited — see the roadmap item about making
the checkpoint self-contained. Generated samples go in `samples/` (override
with `ABLETON_MCP_SAMPLES_DIR`).

## Support expectations

Shared as-is: this is a personal tool I use for my own music. Issues and PRs
are welcome and I read them, but there's no promised response time and no
roadmap commitment. Fork freely — it's MIT.

## License

MIT — see [LICENSE](LICENSE).
