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

## Automation envelopes (2.2, hardened 2.3)

Spike-verified on real Live 12.4.3, no Max for Live required:
- `clip.automation_envelope(param)` → None when absent (CONFIRMED)
- **SESSION CLIPS ONLY** (CONFIRMED via Live's own API docstring, 2.3 audit):
  `automation_envelope` "Returns None for Arrangement clips" — arrangement
  clips carry only modulation; their absolute automation lives on the track's
  automation lanes, which the Python API does not expose. All three envelope
  tools reject `arrangement_clip_index` with a typed error. (The 2.2 claim of
  arrangement support was never real: the mock had one clip class and the
  checkpoint only ever exercised slot 0.)
- **Unwarped audio rejected** (CONFIRMED via LOM): unwarped audio clips
  measure loop bounds in SECONDS and `clip.length` "makes no sense" — beat
  times would be silently wrong. Warp first (`set_clip warping=true`; the
  property exists on audio clips only). Clearing stays allowed (no time math).
- `clip.create_automation_envelope(param)` → envelope object (CONFIRMED)
- `envelope.insert_step(time, length, value)` + `value_at_time` round-trip
  exactly (CONFIRMED); values are in the parameter's NATIVE range — the tools
  normalize 0-1 on the wire like device parameters
- Boundary semantics (CONFIRMED at the 2.2 checkpoint): steps are
  START-EXCLUSIVE — `value_at_time(step_start)` returns the PREVIOUS value,
  and at exactly 0.0 the parameter's live value. get_clip_envelope therefore
  samples at t+0.001 so points read as "value in effect from t".
- `clip.clear_all_envelopes()` no-arg (CONFIRMED); `clip.clear_envelope(param)`
  signature CONFIRMED at the 2.2 checkpoint
- Write discipline (2.3): every point validated (incl. time ≤ clip.length —
  out-of-range times used to be silently squashed into 0.01-beat steps) BEFORE
  the envelope is touched; the envelope is get-or-created BEFORE `clear_first`
  clears, and re-fetched after (whether clearing invalidates a held envelope
  object is unprobed — the re-fetch makes it irrelevant).
- Unused for now: `envelope.create_event/events_in_range/delete_events_in_range`
  — existence CONFIRMED (spike + the installed `_MxDCore\Conversions\
  EnvelopeEvents` module, whose EnvelopeEvent carries time/value/
  control_coefficients incl. curve coefficients), exact signatures unprobed.
  The seam for real curved/ramped automation when wanted; v1 writes steps,
  reads via value_at_time sampling. `clip.automation_envelopes` plural also
  unused (no enumerate tool yet).

Tools: `set_clip_envelope` (points → steps; each holds to the next point;
device param XOR mixer volume/pan target; SESSION clip, MIDI or warped audio),
`get_clip_envelope` (sampled read), `clear_clip_envelopes` (destructive; one
target or all).

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

Two tools live OUTSIDE the registry because they cannot run inside Live:
`get_bridge_status` (server.py) and `get_audio_levels` (mcp_server/m4l.py —
the optional Max for Live tap, see below). Everything else is
registry-generated.

## Hearing (2.3)

Two tiers, deliberately separate:

- **`get_track_meters` (core, all editions):** on-demand snapshot of Live's
  own output meters for every track/return/master — `output_meter_level` is a
  1s hold peak (audio AND MIDI tracks), left/right are momentary (audio-output
  tracks only). Values are Live's 0-1 meter scale, NOT dB, and the tool says
  so. Meters are READ on demand, never observed — the LOM warns stereo meter
  observers add significant GUI load. Answers "is this making sound, roughly
  how loud, are L/R different".
- **`get_audio_levels` (optional, Suite/M4L):** the AbletonMCP Tap v2 device
  on the Master serves calibrated dBFS loudness (stereo power, anti-phase
  safe), a latched sample-peak clipping flag, and 10 resonant octave bands
  31 Hz–16 kHz over TCP 127.0.0.1:9878. Staleness is explicit
  (stale/data_age_ms); any stale patch/js/server combination answers
  available:false with a rebuild hint instead of plausible wrong numbers.
  Design, calibration, honesty notes, and the one-time build/upgrade
  procedure: `m4l/README-lab.md`. Verification: `scripts/tap_checkpoint.py`
  (protocol gate, sine calibration, anti-phase regression).

## Device control depth (2.3)

- `enabled` on set_device_parameters drives the **Device On parameter**
  (looked up by name); `is_active` is get/observe-only and also reflects any
  enclosing Rack's switch (LOM).
- `get_devices` serializes per-parameter `value_items` (human-readable enum
  choices — what "Lowpass"/"24 dB" a normalized value means), `is_enabled`
  (false = macro/live.remote~ owns it; writes are refused up front), and
  `automation_state` (none/active/overridden). `re_enable_automation` on
  set_device_parameters restores automation the batch's writes overrode.
- `insert_device` inserts NATIVE devices by exact name at a chain position
  (Track.insert_device, Live 12.3+) without touching the browser or the
  selected track; plug-ins/M4L/presets still go through browse + load_item.

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

## Reliability protocol (audit hardening; extended in the 2.3 Sol 5.6 round)

- Client auto-resends after a dead connection ONLY for read-only commands
  (registry flags): a response-read failure means the request WAS delivered
  and may have executed. Writes surface "may or may not have executed —
  verify state, retry deliberately."
- The control surface dedupes by request id (ring of 64): a resent id replays
  the cached response instead of executing twice. A Live restart clears the
  ring — which is why the client-side gate exists as well. Timeout responses
  are NEVER cached (2.3): deadline refusal makes a same-id retry safe, so it
  gets a fresh attempt instead of a stale replay.
- **Marshal deadline refusal (2.3):** the scheduled task refuses to START past
  its deadline, and the waiter holds a grace window beyond it — so a timeout
  error means "the Set was NOT modified and never will be by this request".
  The one residual race (task started before the deadline, finished after the
  waiter abandoned) is journaled to operations.jsonl as `late_success`/
  `late_error`; a refused-late task journals `expired`. Before this, a
  timed-out command could still execute later, invisibly.
- **Batch setters validate-then-write (2.3):** set_track / set_transport /
  set_clip / set_device_parameters / set_clip_envelope hoist every offline
  check (index bounds, name lookups, master-track rules, cross-field loop
  bounds, point times) before the first write. Live-side failures mid-apply
  raise `PartialApplyError`, whose message and wire `applied` field name
  exactly which writes landed — atomicity is impossible (Live has no
  rollback), so honesty about partial application is the contract.
- **Two-phase playhead seeks (2.3):** `current_song_time` writes apply only
  between scheduled tasks (repo-verified 12.4) — `transport_control` with a
  position now uses the same `"seeking"` protocol as `create_locator`; the
  MCP server's retry loop is command-agnostic. A seek while already playing is
  a single response (no dependent write). The mock defers seeks identically.
- **Device enabled = the Device On parameter (2.3):** `Device.is_active` is
  get/observe-only in the LOM (and reflects any enclosing Rack); the old
  direct write only ever worked against the mock.
- **stop() closes accepted client sockets (2.3):** the server thread used to
  stay parked in a blocking recv for as long as a client was connected ("did
  not stop cleanly" + 5s stall on every Live script reload). Windows
  cross-thread close surfaces as OSError in the handler = designed shutdown.
- `place_clip_in_arrangement` returns the placed clip + a count — never the
  full timeline (the write path had no size cap; reads cap at 500).
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

- **1.0's audio analyzers/generators.** Hearing now exists (core meters + the
  optional tap, above); offline analyzers (key/tempo/drum detection) may still
  return as a separate optional package; the generators are obsolete — Claude
  composes note lists better than rule-based random walks.
- **Event-level/curved envelopes and arrangement track-lane automation** —
  the former is probe-gated future work (see the envelope section's seam),
  the latter has no exposed API.
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
- After any VERSION bump in pyproject.toml: re-run `uv pip install -e ".[dev]"`
  in BOTH venvs (main and lab). The editable-install hooks are version-named
  and go stale — the symptom is Claude Desktop's launch dying instantly with
  "ModuleNotFoundError: No module named 'mcp_server'" while repo-cwd tests
  stay green (they import from the working directory and never notice).
- JSONL operation journal: `%TEMP%\ableton_mcp_logs\operations.jsonl` — every
  command with params, result, duration; the replay/debug channel.
- `scripts/live_checkpoint.py` is the real-Live regression harness (leaves an
  audible "MCP Test" track). `scripts/smoke_test.py` is the 2-second liveness
  check.
