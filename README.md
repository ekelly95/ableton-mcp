# ableton-mcp

AI agents control Ableton Live. You describe what you want — a drum groove, a
chord progression, a filter sweep, a song arrangement — and your AI assistant
builds it in your Live session while you watch. Works with any MCP client:
Claude Desktop, Claude Code, Codex, or anything else that speaks the Model
Context Protocol over stdio.

**Status: experimental, Windows-only for now** (macOS support is roadmap: the
Live-side script already supports Unix sockets, but the client and installer
don't yet). Verified end-to-end against Ableton Live 12.4 on Windows 11.
Works with **any Live edition** — Intro, Standard, or Suite; no Max for Live
required.

## What the AI can do with it

35 tools: read the whole session at a glance; create/rename/mix tracks;
create, duplicate, launch and edit clips; write MIDI notes by name ("C3",
"F#4" — Ableton convention, C3=60) with per-note chance and velocity spread;
edit single notes precisely by ID without rewriting the clip; set the song's
key and scale; build songs on the Arrangement timeline (create MIDI clips
directly on it, stamp session loops onto it, drop named locators, edit
timeline clips); import audio files into the session or onto the timeline
(the landing pad for sample generation — generators just write a file and
call import_audio); browse Live's library and load instruments/effects; turn
any device knob; control tempo, loop, metronome, playback and (via its own
guarded tool) arrangement recording.

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

Requirements: Windows, Python 3.11+, Ableton Live 11.1+ (12.x verified).

```bash
git clone <this-repo> && cd ableton-mcp
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
python scripts/install_control_surface.py
```

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

Run only ONE MCP client against it at a time (the bridge serves one client,
serially, on purpose).

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
encoding real-Live behaviour verified on 12.4 with provenance comments.
`scripts/live_checkpoint.py` re-verifies the full surface against a running
Live and leaves an audible "MCP Test" track behind. Generated samples go in
`samples/` (override with `ABLETON_MCP_SAMPLES_DIR`).

## License

MIT — see [LICENSE](LICENSE).
