# ableton-mcp

[![Tests](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/ekelly95/ableton-mcp/actions/workflows/tests.yml)

AI agents control Ableton Live. You describe what you want — a drum groove, a
chord progression, a filter sweep, a song arrangement — and your AI assistant
builds it in your Live session while you watch. Works with any MCP client:
Claude Desktop, Claude Code, Codex, or anything else that speaks the Model
Context Protocol over stdio.

What sets this bridge apart from the other Ableton MCP projects (see
[Alternatives](#alternatives)): the agent can **read back the MIDI it wrote**
(stable note IDs, surgical edits), **draw and read real automation envelopes**,
**load third-party VST/VST3 plug-ins**, and — uniquely — **hear the result**
(per-track meters, plus optional calibrated loudness/spectrum metering). It
runs on **any Live edition with no Max for Live required**, and it's MIT
licensed. Trade-offs, stated plainly: one MCP client at a time, and
installation is git-clone rather than one-click.

**Status: experimental, verified on both platforms.** Windows: end-to-end
against Ableton Live 12.4.3 on Windows 11. macOS: full 42-step live checkpoint
passed against Live 12.4.3 (Trial) on an Apple Silicon Mac mini running macOS
Tahoe 26.6 (2026-08-12), over the Unix-socket transport the Mac build uses.
Works with **any Live edition** — Intro, Standard, or Suite; no Max for Live
required.

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
and load instruments/effects — including third-party VST/AU plug-ins and the
presets Live indexes for them (2.4) — or insert native devices directly by name;
search Live's own library database offline — samples, presets, MIDI, grooves,
plug-ins by name/tag/kind, ranked by how often you use them — and rank sounds
by **sonic similarity** to a reference using Live's own audio analysis (2.6);
turn any device knob with its human-readable choices visible; read every
track's output meters (core API — is it making sound, how loud?); control
tempo, loop, metronome, playback and (via its own guarded tool) arrangement
recording; and optionally *hear* the result properly (next section).

## Optional: audio ears (Suite / Max for Live only)

The `get_audio_levels` tool lets the AI *hear* the set — stereo loudness and
peaks in dBFS (anti-phase safe), a latched clipping flag, and a 10-band
octave picture (31 Hz–16 kHz) — via the "AbletonMCP Tap" Max for Live device
on the Main track. Build-once instructions: [m4l/README-lab.md](m4l/README-lab.md).
Without the device the tool simply reports `available: false`; everything
else works on any Live edition (get_track_meters covers coarse level checks
without M4L).

## How it works

```
AI client ── MCP (stdio) ── mcp_server ── TCP 127.0.0.1:9877 ── control surface inside Live
```

Two halves, one source of truth: every tool is generated from the command
registry that the Live-side script executes — nothing is defined twice, and a
schema-hash handshake warns if the two ever drift apart. See
[docs/architecture.md](docs/architecture.md) for design decisions and
verified Live API facts.

## Setup

Requirements: Windows or macOS (both hardware-verified — see Status),
Python 3.11+, [uv](https://docs.astral.sh/uv/), Ableton Live 11.1+ (12.x
verified; `insert_device` needs Live 12.3+ — every other tool works on the
older versions).

```bash
git clone https://github.com/ekelly95/ableton-mcp.git && cd ableton-mcp
uv venv
uv pip install -e ".[dev]"
.venv/Scripts/python.exe scripts/install_control_surface.py
```

On macOS, the venv paths are `.venv/bin/python` and `.venv/bin/ableton-mcp`
wherever this README says `.venv/Scripts/...exe`.

Then, one-time, in Ableton Live: **Options → Preferences → Link, Tempo &
MIDI**, set a free Control Surface dropdown to **AbletonMCP** (Input/Output:
None), and restart Live. Re-run the install script + restart Live after any
update.

Register with your MCP client — e.g. Claude Desktop
(`%APPDATA%\Claude\claude_desktop_config.json`):

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

Run only ONE MCP client against it at a time (the bridge serves one client,
serially, on purpose). `scripts/toggle_desktop_client.py on|off` flips Claude
Desktop's registration so switching between Desktop and Codex is one command.

## Health check

Ask the AI to run `get_bridge_status`, or from a terminal:

```bash
.venv/Scripts/python.exe scripts/smoke_test.py
```

"Cannot connect" almost always means Live isn't running or the control
surface isn't enabled in Preferences.

## Development

```bash
.venv/Scripts/python.exe -m pytest
```

The test suite runs without Live — a mock stands in (`tests/mock_live.py`),
encoding real-Live behaviour verified on 12.4.3 with provenance comments.
`scripts/live_checkpoint.py` re-verifies the full surface against a running
Live and leaves an audible "MCP Test" track behind. Generated samples go in
`samples/` (override with `ABLETON_MCP_SAMPLES_DIR`).

## Alternatives

Three other projects connect AI agents to Ableton Live, each with a different
center of gravity — credit to all of them for mapping this space:

- **[producer-pal](https://github.com/adamjmurray/producer-pal)** — the most
  polished of the field: a compact bar|beat music notation, a note-transform
  language, take lanes, deep APIs for ten native devices, excellent docs and
  one-click install. It runs entirely inside a Max for Live device, so it
  needs Live 12.3+ *with* Max for Live (Suite or the paid add-on), and it's
  GPL-3.0. By its own docs it has no automation/clip-envelope support, can't
  instantiate third-party plug-ins, and has no audio metering — the three
  areas this bridge focuses on.
- **[ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)** — the
  original and by far the most popular. A simple ~22-tool surface with an easy
  uvx install; a good first taste of the idea.
- **[uisato/ableton-mcp-extended](https://github.com/uisato/ableton-mcp-extended)** —
  same remote-script architecture as this project, adds rack/drum-pad
  introspection and a bundled ElevenLabs server. MIDI is write-only (no
  reading notes back) and automation support is limited.

If you want an agent that can revise what it wrote, automate parameters, load
your plug-ins, and check its own mix — that's this project's lane.

## Support expectations

Shared as-is: this is a personal tool I use for my own music. Issues and PRs
are welcome and I read them, but there's no promised response time and no
roadmap commitment. Fork freely — it's MIT.

## License

MIT — see [LICENSE](LICENSE).
