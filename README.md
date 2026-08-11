# ableton-mcp

Claude controls Ableton Live. You describe what you want — a drum groove, a
chord progression, a filter sweep — and Claude builds it in your Live session
while you watch.

This is the 2026 rebuild of the 2025 project, keeping its architecture
(two halves talking over a local socket) and fixing what killed it (the tool
definitions are now generated from one place, never written by hand).

## What Claude can do with it

35 tools: read the whole session at a glance; create/rename/mix tracks; create,
duplicate, launch and edit clips; write MIDI notes by name ("C3", "F#4" —
Ableton convention, C3=60) with per-note chance and velocity spread; edit
single notes precisely without rewriting the clip; set the song's key and
scale; **build songs on the Arrangement timeline** (create MIDI clips directly
on it, stamp session loops onto it, drop named locators, edit timeline clips);
import audio files from disk into the session or onto the timeline (the
landing pad for any future sample generation — generators just write a file to
`samples\`); browse Live's library and load instruments/effects; turn any
device knob; control tempo, loop, metronome, playback and (via its own
guarded tool) arrangement recording.

## Parts

- `control_surface/` — runs **inside Live** (installed as a "Control Surface")
- `mcp_server/` — runs next to Claude Desktop / Claude Code, talks to the above
- One copy of the truth: the MCP server generates its tools from the same
  registry the control surface executes.

## Setup (already done on this machine)

1. `python scripts/install_control_surface.py` — copies the Live-side part to
   your User Library. **Re-run after every code change, then restart Live.**
2. In Live: Options → Preferences → Link, Tempo & MIDI → set a Control Surface
   dropdown to **AbletonMCP** (Input/Output: None). One-time.
3. Claude Desktop: `ableton` entry in `%APPDATA%\Claude\claude_desktop_config.json`
   pointing at `.venv\Scripts\ableton-mcp.exe`. Restart Claude Desktop after edits.

Only run ONE Claude app against it at a time (the bridge serves one client).

## Health check

Ask Claude to run `get_bridge_status`, or from a terminal:

```bash
cd C:/dev/ableton-mcp && .venv/Scripts/python.exe scripts/smoke_test.py
```

"Cannot connect" almost always means Live isn't running or the control surface
isn't enabled in Preferences.

## Development

```bash
cd C:/dev/ableton-mcp && .venv/Scripts/python.exe -m pytest
```

The test suite runs without Live (a mock stands in — see `tests/mock_live.py`, which
encodes real-Live behaviour verified on 12.4.3). `scripts/live_checkpoint.py`
re-verifies everything against a running Live and leaves an audible "MCP Test"
track behind. See `docs/architecture.md` for design decisions and what was
deliberately left out.
