# ableton-mcp 2.0 — Architecture

Rebuild of a 2025 predecessor project (which survives only as a repomix
archive). This doc records what was kept, what changed and why, which Live
API facts are *verified*, and what is deliberately absent. If you are a later
session about to "improve" something here, read the deliberate-decisions list
first.

## Shape

```
Claude ── MCP (stdio) ── mcp_server ── TCP 127.0.0.1:9877 ── control_surface ── Live API
                         (own process)  length-prefixed JSON   (inside Live 12)
```

- Wire format (unchanged from 1.0): 4-byte big-endian length + UTF-8 JSON.
  Request `{type, params, id}`; response `{status, result|error, error_type, id}`.
- The control surface knows nothing about MCP; the MCP server knows nothing
  about Live's internals. This isolation is what allows the Live-side to be
  reimplemented later (e.g. on Ableton's official Extensions SDK, beta since
  June 2026, Suite-only, still can't do transport) without touching the rest.

## The one rule: single source of truth

`control_surface/registry.py` holds every command: typed input schemas,
output schemas, read-only/destructive flags, per-command timeouts.
`mcp_server/server.py` **imports that registry** and generates `types.Tool`
objects from it. There is no hand-written tool list anywhere. 1.0 died of
exactly that duplication (a 1,245-line tools.py whose schemas were never
actually wired into FastMCP). Guards that keep it true:

- `tests/test_import_purity.py` — registry+commands must import in a pristine
  interpreter (no `Live`, no `_Framework`), because the MCP server process
  imports them.
- Version + schema-hash handshake in `ping`; the server warns on mismatch with
  the copy deployed inside Live (re-run the install script, restart Live).
- Registry refuses duplicate command names.

## Threading (hard-won)

Live's API is main-thread-only. The socket server (daemon thread) marshals
EVERY handler — reads included — as ONE task per request onto Live's main
thread via `schedule_message`, then blocks on a queue with that command's
timeout (`thread_marshal.py`).

Verified the hard way on 12.4.3:

- **Do not override `update_display()`** in the ControlSurface subclass.
  _Framework pumps scheduled messages inside it; an empty override silently
  kills all marshaling (symptom: ping answers, every real command times out).
- `schedule_message(1, task)`, not 0 — some _Framework versions assert
  delay > 0, and both land on the same ~100ms tick.
- The ~100ms tick is a per-request latency floor → the tool surface is
  batch-first (arrays of notes/params/sends per call) and payload-bounded
  (`get_session_overview` excludes notes; `get_notes` caps at 2000).

## Live API facts (verified on Live 12.4.3 Trial, 2026-08-10)

Encoded in `tests/mock_live.py` with provenance comments — keep them in sync:

1. `get_notes_extended(from_pitch, pitch_span, from_time, time_span)` —
   pitch-first argument order.
2. `MidiNoteSpecification` accepts pitch/start_time/duration/velocity/mute/
   probability/velocity_deviation/release_velocity kwargs.
3. `add_new_notes` returns nothing useful — re-fetch to learn note IDs.
4. `apply_note_modifications` REJECTS Python tuples/lists — you must pass the
   native vector returned by `get_notes_extended`, with notes mutated in place
   (fetch-modify-apply). This is why `update_notes` passes the whole vector.
5. `remove_notes_by_id(tuple_of_ints)` and `remove_notes_extended(pitch-first)`
   both work as named.
6. The master track is "Main" in Live 12 and has NO mute/solo/arm properties —
   reading them raises.
7. Mixer: volume normalized 0–1 with 0.85 ≈ 0 dB; pan −1..1 (exposed 0–1 with
   0.5 center); sends 0–1.
8. Browser loading targets the SELECTED track — `load_item` sets
   `song.view.selected_track` first; works from a scheduled task.
9. First browser load in a fresh Live session can exceed 60s (Core Library
   warm-up) — `load_item` has a 120s budget and the client grants heavy
   commands their declared budget + grace instead of its flat timeout.
10. `duplicate_clip_slot(i)` copies into slot i+1.

## Automation envelopes (2.2 — built on the 2026-08-11 spike findings)

Spike-verified on real Live 12.4.3, no Max for Live required:
- `clip.automation_envelope(param)` → None when absent (CONFIRMED)
- `clip.create_automation_envelope(param)` → envelope object (CONFIRMED)
- `envelope.insert_step(time, length, value)` + `value_at_time` round-trip
  exactly (CONFIRMED); values are in the parameter's NATIVE range — the tools
  normalize 0-1 on the wire like device parameters
- Boundary semantics (CONFIRMED at the 2.2 checkpoint): steps are
  START-EXCLUSIVE — `value_at_time(step_start)` returns the PREVIOUS value,
  and at exactly 0.0 the parameter's live value. get_clip_envelope therefore
  samples at t+0.001 so points read as "value in effect from t".
- `clip.clear_all_envelopes()` no-arg (CONFIRMED); `clip.clear_envelope(param)`
  signature VERIFY at checkpoint
- Unused for now: `envelope.create_event/events_in_range/delete_events_in_range`
  (shape unknown — v1 writes steps, reads via value_at_time sampling),
  `clip.automation_envelopes` plural (shape unknown — no enumerate tool yet)

Tools: `set_clip_envelope` (points → steps; each holds to the next point;
device param XOR mixer volume/pan target; session or arrangement clip),
`get_clip_envelope` (sampled read), `clear_clip_envelopes` (destructive; one
target or all). Envelopes work on audio clips too, not just MIDI.

## Client discipline (`mcp_server/client.py`)

- One persistent connection, `threading.Lock` around send: the control surface
  serves ONE client serially, so concurrent tool calls queue client-side.
- Reconnect-and-resend exactly once, and ONLY on connection errors (OSError).
  Never after a timeout — the command may still be executing inside Live and a
  resend would run it twice.
- stdout is sacred (MCP stdio). All logging everywhere goes to stderr or files;
  `tests/test_server_tools.py` asserts importing the server writes nothing to
  stdout. Inside Live, stderr still reaches Log.txt.

## MCP surface

SDK pinned `mcp>=1.29,<2` (v2.0.0 shipped 2026-07-28 for the stateless spec —
adopt later as a deliberate upgrade, not in passing). Low-level `Server` API on
purpose; FastMCP infers schemas from function signatures, which is the exact
trap 1.0 fell into. Tools carry outputSchema (structured output) and
readOnly/destructive annotations. Handlers return dicts → SDK emits
structuredContent + JSON text. Errors raise → proper isError tool results.

## Arrangement view (2.1)

- TWO composition routes (both LOM-confirmed): `create_arrangement_clip` →
  `Track.create_midi_clip(start_time, length)` creates empty MIDI directly on
  the timeline (the direct/autonomous route; audit correction — we first
  believed this was impossible); and `place_clip_in_arrangement` →
  `Track.duplicate_clip_to_arrangement` (loop-then-stamp; its return value is
  UNdocumented, so the code uses the return when truthy and otherwise re-scans
  by start_time with epsilon — the list is time-ordered).
- Arrangement clip indices are POSITIONAL and go stale on any change; the
  destructive delete takes `expected_start_time` as a stale-index guard.
- Note commands address a clip in EITHER view: exactly one of `slot_index` /
  `arrangement_clip_index` (enforced in `resolve_clip_ref`, not the schema —
  our generator has no oneOf).
- `create_locator` guards against `set_or_delete_cue`'s toggle semantics
  (creating where a cue exists would DELETE it) and moves the playhead.
- `back_to_arranger` is the classic silent failure: after session clips play,
  the timeline stays overridden until it's set false. place_clip echoes it.

## Sample-generation seam (2.1)

`import_audio` lands an audio file on an audio track — arrangement route
(`Track.create_audio_clip(abs_path, position)`) or session route
(`ClipSlot.create_audio_clip(abs_path)`, audit correction: session import IS
possible); absolute paths only — Live resolves relative paths against its
install dir. Generation providers are deliberately CLIENT-side: anything that
writes an audio file (convention: `samples\`) lands it with this one command.
No provider dependency ever goes into the bridge.

## Reliability protocol (audit hardening)

- Client auto-resends after a dead connection ONLY for read-only commands
  (registry flags): a response-read failure means the request WAS delivered
  and may have executed. Writes surface "may or may not have executed —
  verify state, retry deliberately."
- The control surface dedupes by request id (ring of 64): a resent id replays
  the cached response instead of executing twice. A Live restart clears the
  ring — which is why the client-side gate exists as well.
- `arrangement_record` is its own destructive-annotated tool, kept out of
  set_transport on purpose (record + play overwrites the timeline).
- `delete_arrangement_clip` REQUIRES `expected_start_time` — positional
  indices go stale, and a destructive command must not accept a stale one.

## Pitch names & key/scale (2.1)

- ABLETON convention everywhere: **C3 = 60** (most other software says C4=60).
  `utils/pitch.py` parses names; note output carries `pitch_name`; tool
  descriptions state the convention loudly.
- Song key/scale via `set_transport` (`scale_root`/`scale_name`/`scale_mode`);
  `scale_name` is pass-through with a read-back postcondition (unknown names
  must not silently no-op).

## Deliberately absent (do not "fix" without a decision)

- **Audio engine** (1.0's analyzers/generators). Analyzers (key/tempo/drum
  detection) may return as a separate optional package; the generators are
  obsolete — Claude composes note lists better than rule-based random walks.
- **Run-arbitrary-code-in-Live escape hatch** (the 2026 community trend).
  Deferred: crash risk inside Live needs deliberate sandboxing design. The
  registry makes it one more command when wanted.
- **Quantize, routing, capture MIDI, warp control, return-track deletion,
  arrangement clip move/resize** (delete+re-place is the v1 workaround).
  (Arrangement view itself shipped in 2.1.)
- **Multi-client socket server.** One serial client is a feature (predictable
  ordering); the MCP server is the only intended client.
- Old project's melody/drum generators, hand-written tools.py, thread-affinity
  decorators, legacy `get_notes`/`set_notes` API: all intentionally not carried
  over.

## Operational notes

- Install script probes User Library (incl. OneDrive-redirected Documents) then
  per-install ProgramData; copies OVER existing files (never rmtree — OneDrive
  and Ableton's indexer hold locks even when Live is closed).
- After any control_surface change: re-run install script AND restart Live.
- JSONL operation journal: `%TEMP%\ableton_mcp_logs\operations.jsonl` — every
  command with params, result, duration; the replay/debug channel.
- `scripts/live_checkpoint.py` is the real-Live regression harness (leaves an
  audible "MCP Test" track). `scripts/smoke_test.py` is the 2-second liveness
  check.
