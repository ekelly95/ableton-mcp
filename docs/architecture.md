# ableton-mcp — Architecture

This document records the architecture, verified Live API behavior, and design
exclusions. Review the recorded decisions before changing the corresponding
behavior.

## Shape

```
MCP client ── stdio ── mcp_server ── local socket ── control_surface ── Live API
                         (own process)  length-prefixed JSON     (inside Live)
```

- Wire format (unchanged from 1.0): 4-byte big-endian length + UTF-8 JSON.
  Request `{type, params, id}`; response `{status, result|error, error_type, id}`.
- The control surface knows nothing about MCP; the MCP server knows nothing
  about Live's internals. This isolation is what allows the Live-side to be
  reimplemented later (e.g. on Ableton's official Extensions SDK — beta since
  June 2026, Suite-only, and not yet exposing transport control) without
  touching the rest.

## Trust model

One machine, one user, no authentication. Both listeners — the bridge on TCP
`127.0.0.1:9877` (Windows) or `/tmp/ableton_mcp.sock` (macOS), and the optional
tap on TCP `127.0.0.1:9878` — accept commands from anything that can open a
loopback socket. There is no token, no peer check, and no allowlist. Anything
running on the machine can therefore drive Live through the full command
surface, including the destructive parts, while Live is open with the control
surface enabled.

This is a deliberate choice, not an omission. The alternative — a shared secret
written by the installer — would live in a file readable by anything running as
the same user, so it would stop other *accounts* on the machine and nothing
else, at the cost of an install step and a new way for the two halves to
disagree. The single-user assumption is the honest description of the tool.

What follows from it, and is enforced:

- Loopback only, never `0.0.0.0`. Neither listener is reachable off the machine.
- On Windows the TCP socket sets `SO_EXCLUSIVEADDRUSE`, **not** `SO_REUSEADDR`.
  Windows inverts that option's meaning: with `SO_REUSEADDR` any other process
  can bind the same port and take over new connections (measured — the second
  bind succeeds). A hijacker would both see every command and choose the
  replies, and replies land in an AI agent's context. The exclusive option
  refuses the second bind and still rebinds immediately after a script reload,
  so it costs nothing.
- The Unix socket is mode 0600, and logs live under the user's own directory
  (see Operational notes). Group access bought nothing and, since every macOS
  account is in `staff`, cost a great deal.
- The single-user assumption stops at the *machine* boundary — it is not an
  assumption that the data is trustworthy. Names, paths and tags read out of a
  Live set or the library are attacker-supplied text as far as an agent is
  concerned; see `AGENTS.md`.

## Single source of truth

`control_surface/registry.py` holds every command: typed input schemas,
output schemas, read-only/destructive flags, per-command timeouts.
`mcp_server/server.py` **imports that registry** and generates `types.Tool`
objects from it. There is no separate hand-written tool list. An earlier
version of this project carried a 1,245-line `tools.py` whose schemas were not
wired into FastMCP, which allowed the declared and implemented surfaces to
drift. Current guards:

- `tests/test_import_purity.py` — registry+commands must import in a pristine
  interpreter (no `Live`, no `_Framework`), because the MCP server process
  imports them.
- Version + schema-hash handshake in `ping`; the server warns on mismatch with
  the copy deployed inside Live (re-run the install script, restart Live).
- Registry refuses duplicate command names.

## Threading

Live's API is main-thread-only. The socket server (daemon thread) marshals
every handler, including reads, as one task per request onto Live's main
thread via `schedule_message`, then blocks on a queue with that command's
timeout (`thread_marshal.py`).

Verified behavior on Live 12.4.3:

- Do not override `update_display()` in the ControlSurface subclass.
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
4. `apply_note_modifications` rejects Python tuples/lists; it requires the
   native vector returned by `get_notes_extended`, with notes mutated in place
   (fetch-modify-apply). This is why `update_notes` passes the whole vector.
5. `remove_notes_by_id(tuple_of_ints)` and `remove_notes_extended(pitch-first)`
   both work as named.
6. The master track is "Main" in Live 12 and has NO mute/solo/arm properties —
   reading them raises.
7. Mixer: volume normalized 0–1 with 0.85 ≈ 0 dB; pan −1..1 (exposed 0–1 with
   0.5 center); sends 0–1.
8. Browser loading targets the selected track; `load_item` sets
   `song.view.selected_track` first; works from a scheduled task.
9. First browser load in a fresh Live session can exceed 60s (Core Library
   warm-up) — `load_item` has a 120s budget and the client grants heavy
   commands their declared budget + grace instead of its flat timeout.
10. `duplicate_clip_slot(i)` copies into slot i+1.

## Automation envelopes (2.2, hardened 2.3)

Verified on real Live 12.4.3 with no Max for Live dependency:
- `clip.automation_envelope(param)` → None when absent.
- **Session clips only** (verified against Live's API and a real session):
  `automation_envelope` "Returns None for Arrangement clips" — arrangement
  clips carry only modulation; their absolute automation lives on the track's
  automation lanes, which the Python API does not expose. All three envelope
  tools reject `arrangement_clip_index` with a typed error. The earlier 2.2
  claim of arrangement support was incorrect because the mock used one clip
  class and the checkpoint exercised only a Session slot.
- **Unwarped audio is rejected** (verified against the LOM): unwarped audio clips
  measure loop bounds in seconds and `clip.length` "makes no sense" — beat
  times would be silently wrong. Warp first (`set_clip warping=true`; the
  property exists on audio clips only). Clearing stays allowed (no time math).
- `clip.create_automation_envelope(param)` → envelope object.
- `envelope.insert_step(time, length, value)` + `value_at_time` round-trip
  exactly; values are in the parameter's native range — the tools
  normalize 0-1 on the wire like device parameters.
- Boundary semantics verified at the 2.2 checkpoint: steps are
  start-exclusive — `value_at_time(step_start)` returns the previous value,
  and at exactly 0.0 the parameter's live value. get_clip_envelope therefore
  samples at t+0.001 so points read as "value in effect from t".
- `clip.clear_all_envelopes()` takes no argument; the
  `clip.clear_envelope(param)` signature was verified at the 2.2 checkpoint.
- Write discipline (2.3): every point validated (incl. time ≤ clip.length —
  out-of-range times used to be silently squashed into 0.01-beat steps) before
  the envelope is touched; the envelope is obtained or created before `clear_first`
  clears, and re-fetched after (whether clearing invalidates a held envelope
  object is unprobed — the re-fetch makes it irrelevant).
- Unused for now: `envelope.create_event/events_in_range/delete_events_in_range`
  — existence verified by a spike and the installed `_MxDCore\Conversions\
  EnvelopeEvents` module, whose EnvelopeEvent carries time/value/
  control_coefficients incl. curve coefficients; exact signatures unprobed.
  The seam for real curved/ramped automation when wanted; v1 writes steps,
  reads via value_at_time sampling. `clip.automation_envelopes` plural also
  unused (no enumerate tool yet).

Tools: `set_clip_envelope` (points → steps; each holds to the next point;
device param XOR mixer volume/pan target; Session clip, MIDI or warped audio),
`get_clip_envelope` (sampled read), `clear_clip_envelopes` (destructive; one
target or all).

## Client discipline (`mcp_server/client.py`)

- One persistent connection, `threading.Lock` around send: the control surface
  serves one client serially, so concurrent tool calls queue client-side.
- Reconnect-and-resend exactly once, and only on connection errors (OSError).
  Never after a timeout — the command may still be executing inside Live and a
  resend would run it twice.
- Failure translation, in the order the client meets them: a dead connection
  becomes `AbletonConnectionError` with the "is Live running" hint; a timeout
  becomes `AbletonConnectionError` naming the wait, connection reset; an
  undecodable payload and a decodable-but-wrong-shaped reply both become
  `AbletonConnectionError` asking whether something else holds the port
  (2.8.1); only a well-formed `{"status": "error"}` becomes `CommandError`.
  That last boundary carries weight — `CommandError` is the only one that
  asserts Live actually ran the command.
- stdout is reserved for MCP stdio. All logging goes to stderr or files;
  `tests/test_server_tools.py` asserts importing the server writes nothing to
  stdout. Inside Live, stderr still reaches Log.txt.

## MCP surface

SDK pinned `mcp>=1.29,<2` (v2.0.0 shipped 2026-07-28 for the stateless spec;
adoption requires a planned compatibility upgrade). The low-level `Server` API
keeps the shared registry authoritative rather than inferring schemas from
function signatures. Tools carry outputSchema and readOnly/destructive
annotations. Handlers return dicts → SDK emits
structuredContent + JSON text. Errors raise → proper isError tool results.

Five tools live outside the registry because they cannot (or need not) run
inside Live: `get_bridge_status` (server.py), `get_audio_levels`
(mcp_server/m4l.py — the optional Max for Live tap, see below),
`transform_clip` (mcp_server/transforms.py, 2.5), and `search_library` /
`find_similar` (mcp_server/library.py, 2.6). Everything else is
registry-generated.

## Audio metering (2.3)

Two metering tiers are available:

- **`get_track_meters` (core, all editions):** on-demand snapshot of Live's
  own output meters for every track/return/master — `output_meter_level` is a
  1s hold peak for audio and MIDI tracks; left/right are momentary on tracks
  with audio output. Values are Live's 0-1 meter scale rather than dB. Meters
  are read on demand and never observed continuously; the LOM warns stereo meter
  observers add significant GUI load. Answers "is this making sound, roughly
  how loud, are L/R different".
- **`get_audio_levels` (optional, Suite/M4L):** the AbletonMCP Tap v2 device
  on the Main track serves calibrated dBFS loudness (stereo power, anti-phase
  safe), a latched sample-peak clipping flag, and 10 resonant octave bands
  31 Hz–16 kHz over TCP 127.0.0.1:9878. Staleness is explicit
  (stale/data_age_ms); any stale patch/js/server combination answers
  available:false with a rebuild hint instead of plausible wrong numbers.
  Design, calibration, limitations, and the one-time build/upgrade
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
- `insert_device` inserts native devices by exact name at a chain position
  (Track.insert_device, Live 12.3+) without touching the browser or the
  selected track; plug-ins/M4L/presets still go through browse + load_item.

## Plug-in browser root (2.4 — probed on real Live 12.4.3, 2026-08-12)

- `browse`/`load_item` reach third-party plug-ins via the `plugins` root (LOM
  `Browser.plugins`), closing the gap where insert_device's own error text
  pointed at a route that didn't exist. This removes the need for the
  unsupported screen-automation workaround used before the root was exposed.
- Verified tree shape: `plugins` → format folder (`VST`, `VST3`) → vendor
  folder → loadable plugin item. Both installed plug-ins load via `load_item`:
  `['plugins','VST','SonicCat','Purity_x64']` (VST2) and
  `['plugins','VST3','Spectrasonics','Omnisphere']`. Loading onto a fresh MIDI
  track auto-renames it after the plugin.
- Plugin items can carry children (presets Live indexes for them), and the mock
  encodes that loadable-with-children shape. For the two installed plug-ins,
  children are empty: Omnisphere's STEAM library and Purity's bank
  picker live inside the plugin UI and are invisible to Live's browser. Patch
  choice there remains a human step.
- A freshly loaded PluginDevice exposes `parameter_count: 1`
  ("Device On") until the user hand-clicks Configure in Live — so parameter
  writes are not a patch-selection route either.

## Token economy (2.5)

- **Server-side dialects.** The note notation (mcp_server/notation.py) and the
  transform language (mcp_server/transforms.py) are expanded/applied in the
  MCP server before anything crosses the wire — Live only ever sees note
  dicts. Keeping these languages server-side avoids a control-surface
  reinstall when they change. Their params (`notation`, `transforms`,
  `format`) are declared in the shared registry for schema-hash
  symmetry; Live-side handlers accept and ignore them so a drifted server
  can't crash a command.
- **transform_clip is a server-side composite tool** (declared next to
  get_bridge_status, outside the registry): get_notes → apply_transforms →
  remove/update/add diffed by note_id. Outside-the-registry means no schema
  hash impact and no Live code.
- **Compact emission.** A bare dict return makes the MCP SDK serialize the
  payload twice, the text copy at indent=2 (measured 1.5-1.9x). _tool_result
  in mcp_server/server.py emits compact text + the dict for output_schema
  validation. Never return a bare dict from call_tool.
- **Absent = default.** Note fields (mute/probability/velocity_deviation/
  release_velocity) and device-parameter fields (is_quantized/is_enabled/
  automation_state) at Live defaults are omitted from reads; derivable
  arrangement-clip fields (end_time, is_audio_clip) are never emitted; the
  session overview omits empty clip slots and sends unless asked. Stated in
  the affected tool descriptions.
- Measured on the 12-track/16-scene mock: session overview 54,687 chars
  (old emission, old shape) → 3,784 (93% less); 76-note pattern written as
  notation 52 chars vs 4,850 JSON; read back compact 185 chars vs 19,076
  old emission.

## Master/return device addressing (2.8)

The previous regular-track-only limitation was removed:
`track_type` ("track" default | "return" | "master") on get_devices,
set_device_parameters, insert_device, delete_device, and load_item, resolved
through the existing `resolve_track` ("master" ignores track_index). The
following behavior was verified on Live 12.4.3 (2.8 checkpoint, 42/42): return-chain devices
read and written; native insert onto Main; `load_item` reaches Main by
selecting the master track first (`song.view.selected_track = master_track`
works) — the route plug-ins like Ozone need. Return-track deletion is not
supported; only their devices are addressable.

## Take lanes + deep native-device control (2.7)

These changes shipped as one Live-side batch so the registry hash moved once.
The facts below were verified on Live 12.4.3 at the 2.7 checkpoint (41/41,
which also cleared the full-checkpoint re-run pending since 2.3.0) unless
marked otherwise.

**Take lanes** — stack MIDI variations on one arrangement track:
- `Track.create_take_lane()` appends; `TakeLane.name` settable; lane clips
  are created and edited on the lane object (`lane.create_midi_clip`), and
  `Track.arrangement_clips` excludes lane clips. This main-lane exclusion is
  what makes per-lane indices coherent.
- Live exposes no delete API for take lanes, so they remain for the session.
  The bridge enforces a cap of 8 per track. Live's own cap was not probed
  because the test would create persistent lanes.
- Surface: `create_take_lane`; per-track `take_lanes` (name + clips) in
  `get_arrangement`, absent when none; `take_lane_index` on
  create_arrangement_clip, the five clip-ref commands, and transform_clip —
  it redirects `arrangement_clip_index` to count within that lane.
- Unprobed: lane-clip list time-ordering with multiple clips per lane
  (a verification comment remains in the mock); lane-clip deletion has no route
  (neither lane-scoped nor track-scoped) — a lane clip outlives the session
  or dies with its track.

**Device class-property tables** (`_CLASS_PROPS` in commands/devices.py) —
the state that never appears in `device.parameters`:
- Keyed by `Device.class_name`: Simpler is `OriginalSimpler`, EQ Eight is
  `Eq8`, and Drift is `Drift`.
- Kinds: bool, int, enum (fixed labels from the LOM docs), and indexed —
  Drift's `<name>_index` properties paired with runtime-read `<name>_list`
  StringVectors, so its vocabularies always come from the running Live.
  Verified lists include voice modes Poly/Mono/Stereo/Unison and mod sources
  Env 1/Env 2/LFO/Key/Vel/Mod/Press/Slide.
- `get_devices` emits `class_properties` + gate-filtered `class_methods`
  when it knows the class (absent otherwise); `set_device_parameters`
  accepts the names transparently (label, bool, or int — validate-then-write
  preserved) and runs sample ops via `invoke`
  (reverse/crop/warp_as/warp_double/warp_half/guess_playback_length, each
  gated by its `can_*` property where one exists). On a one-shot 808:
  can_warp_as true, can_warp_double/half false; guess_playback_length
  returned beats.
- Live constraint found by the checkpoint and encoded in the mock:
  `Track.insert_device` refuses a second instrument per chain — "Device
  chains cannot have more than one instrument each". Loading a sample/
  instrument via load_item replaces it instead (2.4 behaviour); insert_device
  raises.
- Adding a device to the table is data, not code: extend `_CLASS_PROPS`,
  give the mock a class device, probe on real Live before claiming it.

## Library intelligence (2.6)

Two server-only tools (`search_library`, `find_similar` in mcp_server/
library.py, declared beside transform_clip — outside the registry hash, no
Live code, work with Live closed) read Live's own library SQLite databases at
`%LOCALAPPDATA%\Ableton\Live Database\` (macOS:
`~/Library/Application Support/Ableton/Live Database`; override
`ABLETON_MCP_LIVE_DB_DIR`). Facts probed on the real DBs
(scripts/probe_library_db.py, Live 12.4.3 Trial, 2026-08-12) and pinned in
tests/test_library.py's fixture schema:

- Selection: `Live-files-*.db` highest number in the filename (mtime
  tie-break); `Live-plugins-*.db` newest mtime (numbers not monotonic). Open
  `file:...?mode=ro&immutable=1` (URI-quoted because the directory name has a
  space). This was verified safe while Live runs: writes are refused and reads see the last
  checkpointed snapshot, and a `-wal` newer than the `.db` becomes a
  `staleness` note, never an error.
- `files` is the whole tree: folders, items, and tag rows; `file_type` is a
  big-endian FourCC int ('wav-','aiff','oggv','adv-','adg-','amp-','alc-',
  'midi','als-','agr-','keyw','fldr','plug',...). The kind enum maps from
  that whitelist. Paths reconstruct by a recursive CTE up `parent_id` (the
  drive root row is literally 'C:\'); reconstructed paths were verified
  against files on disk.
- `places` has exactly six rows keyed by **file_id** (no place_id column):
  folder_kind 0=Core Library, 1=User Library, 4=Current Project, 8=Built-in,
  9=Cloud, 10=Plugins → the source enum.
- `keywords(file_id, keyw_id)` joins files to tag rows that are themselves
  files rows of type 'keyw' ("One Shot", "Punchy", ...). Tag filters
  are combined with case-insensitive AND matching.
- `fe_values` joins on **file_id** (the `hash` column is opaque, never a
  key); `data` is 268 bytes: uint32 LE version=18, count=64, reserved, then
  64 float32 LE — Live's own audio feature vector. Cosine over all 6,199
  vectors takes ~0.04 s in pure Python; find_similar ranks with it. One
  verification result ranked Impulse 606 and 808 Core Kit near Impulse 808.
- `search_aggregation*` are FTS tables on a custom AbletonTokenizer that
  raises outside Live — search uses plain LIKE (wildcards escaped).
- Plugins DB `plugins` row shapes: `device:vst:instr:<num>?n=<name>` (VST2),
  `device:vst3:<instr|audiofx>:<uuid>`; `subcategories` pipe-delimited;
  disabled rows excluded.

**Browser-path mapping:** `browser_path_guess` is emitted only where the
mapping has been verified:
user_library items (path segments relative to the User Library root mirror
the browse tree exactly; a search → browse → load_item check landed a kit
sample as a Simpler) and plugins (['plugins', VST|VST3, vendor, name] built
from the plugins DB, not from the files DB's virtual `<plugins>/` paths,
which contain an extra 'Custom' level the real browser tree doesn't show).
Core Library items appear in the browser by category, not disk layout — no
guess, absolute path only. `import_audio` accepts the
forward-slash absolute paths exactly as search_library returns them.

## macOS status (shipped in 2.6; hardware-verified 2026-08-12 on the 2.8 checkpoint)

The client mirrors the control surface's transport branch: TCP on Windows and
AF_UNIX on macOS (`use_tcp` and `socket_path` remain constructor parameters so
tests can force either mode). The installer recognizes
`~/Music/Ableton/User Library` and `/Applications/Ableton Live*.app` bundles.
CI runs the full suite on `macos-latest`, including the client and control
surface together over a real Unix socket.

The full 42-step checkpoint passed against Live 12.4.3 Trial on an Apple
Silicon Mac mini M2 Pro running macOS Tahoe 26.6.1. The optional Max for Live
tap also passed its 5-step protocol and calibration checkpoint. Remaining
unknowns are a full GUI MCP-client session, long-running stability, and privacy
prompts on a normally configured personal Mac; stdio launch and handshake are
covered by the automated suite.

## Arrangement view (2.1)

- Two composition routes are verified against the LOM: `create_arrangement_clip` →
  `Track.create_midi_clip(start_time, length)` creates empty MIDI directly on
  the timeline; and `place_clip_in_arrangement` →
  `Track.duplicate_clip_to_arrangement` (loop-then-stamp; its return value is
  undocumented, so the code uses the return when truthy and otherwise re-scans
  by start_time with epsilon — the list is time-ordered).
- Arrangement clip indices are positional and go stale on any change; the
  destructive delete takes `expected_start_time` as a stale-index guard.
- Note commands address a clip in either view: exactly one of `slot_index` or
  `arrangement_clip_index`. `resolve_clip_ref` enforces the condition because
  the schema generator does not emit `oneOf`.
- `create_locator` guards against `set_or_delete_cue`'s toggle semantics
  because creating where a cue exists would delete it, and moves the playhead.
- `back_to_arranger` is a common silent failure: after Session clips play,
  the timeline stays overridden until it's set false. place_clip echoes it.

## Sample-generation seam (2.1)

`import_audio` lands an audio file on an audio track — arrangement route
(`Track.create_audio_clip(abs_path, position)`) or session route
(`ClipSlot.create_audio_clip(abs_path)`); Session import is supported. Paths
must be absolute because Live resolves relative paths against its install
directory. Generation providers remain client-side: anything that
writes an audio file (convention: `samples\`) lands it with this one command.
No provider dependency ever goes into the bridge.

## Reliability protocol

- Client auto-resends after a dead connection only for read-only commands
  (registry flags): a response-read failure means the request may have been delivered
  and may have executed. Writes surface "may or may not have executed —
  verify state, retry deliberately."
- The control surface dedupes by request id (ring of 64): a resent id replays
  the cached response instead of executing twice. A Live restart clears the
  ring, which is why the client-side gate also exists. Two carve-outs: timeout
  responses are not cached (deadline refusal makes a same-id retry safe, so it
  gets a fresh attempt instead of a stale replay), and an id that is not a
  string is treated as no id at all — the request runs and answers under a
  minted id, but nothing is cached under a key a retry could never match.
  Using the raw id as a dict key meant an unhashable one (a list) raised
  TypeError and hung up the connection instead of answering (2.8.1).
- **Wrong-shaped frames are answered, not crashed on (2.8.1):** a request body
  that parses to something other than a JSON object is refused by name. `null`
  in particular used to parse to None, which the connection handler reads as
  end-of-stream — the peer was hung up on with no answer at all. Symmetrically,
  a *reply* that is not the bridge protocol (empty frame, bare array, object
  without `status`) raises `AbletonConnectionError`, never `CommandError`:
  CommandError means "Live executed this and refused it", and saying that
  about a destructive write which never arrived tells the model the session is
  untouched when it has no idea. Both directions now name the likely cause —
  something other than the control surface on the port.
- **Marshal deadline refusal (2.3):** the scheduled task refuses to start past
  its deadline, and the waiter holds a grace window beyond it — so a timeout
  error means the request did not modify the Set and will not execute later.
  The one residual race (task started before the deadline, finished after the
  waiter abandoned) is journaled to operations.jsonl as `late_success`/
  `late_error`; a refused-late task journals `expired`. Before this, a
  timed-out command could still execute later, invisibly.
- **Batch setters validate-then-write (2.3):** set_track / set_transport /
  set_clip / set_device_parameters / set_clip_envelope hoist every offline
  check (index bounds, name lookups, master-track rules, cross-field loop
  bounds, point times) before the first write. Live-side failures mid-apply
  raise `PartialApplyError`, whose message and wire `applied` field name
  exactly which writes landed. Live has no rollback, so partial application
  must be reported explicitly.
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
- `delete_arrangement_clip` requires `expected_start_time` because positional
  indices go stale, and a destructive command must not accept a stale one.

## Pitch names & key/scale (2.1)

- Ableton pitch convention: **C3 = 60** (many other applications use C4=60).
  `utils/pitch.py` parses names; note output carries `pitch_name`; tool
  descriptions state the convention explicitly.
- Song key/scale via `set_transport` (`scale_root`/`scale_name`/`scale_mode`);
  `scale_name` is pass-through with a read-back postcondition (unknown names
  must not silently no-op).

## Design exclusions

- **Tool consolidation (many typed tools → a few generic tools).** Object type
  is clearer in a tool name than in a flexible parameter, particularly for
  destructive operations, and separate tools preserve permission granularity.
  Reconsider consolidation only with an evaluation harness that can measure
  tool-selection regressions.
- **Built-in audio generators.** Clients can supply note data or generated
  audio through the existing MIDI and import tools. Offline key, tempo, or drum
  analysis may be added later as a separate optional package.
- **Event-level or curved envelopes.** Exact Live API signatures still require
  a real-session probe. Arrangement automation lanes are not exposed by the
  available API.
- **An unrestricted run-arbitrary-code tool inside Live.** Executing arbitrary
  code in Live carries crash and data-loss risk and would require a separate
  sandbox design.
- **Quantize, routing, capture MIDI, return-track deletion, and
  arrangement clip move/resize.** These are not currently exposed as tools;
  delete-and-replace remains the arrangement workaround. (Warp control left
  this list: `set_clip` toggles `warping`, and Simpler's warp/crop operations
  run through guarded `invoke` actions.)
- **Multiple simultaneous MCP clients.** One serial client preserves operation
  ordering.

## Operational notes

- The install script checks the User Library (including OneDrive-redirected
  Documents) before per-install ProgramData locations. It copies over existing
  files rather than deleting the target because OneDrive and Ableton's indexer
  may hold locks after Live closes.
- After a `control_surface` change, rerun the install script and restart Live.
- After a version change in `pyproject.toml`, reinstall the editable package in
  the active virtual environment. Stale editable-install hooks can cause an MCP
  client to report `ModuleNotFoundError: No module named 'mcp_server'` even
  while repository-local tests still pass.
- JSONL operation journal: `%TEMP%\ableton_mcp_logs\operations.jsonl` on
  Windows, `~/Library/Logs/AbletonMCP/operations.jsonl` on macOS — every command
  with params, result, duration; the replay/debug channel. The directory is
  created 0700 and is deliberately per-user: this journal is a full record of a
  session, and it used to sit in world-readable, world-writable `/tmp`.
- `scripts/live_checkpoint.py` is the real-Live regression harness (leaves an
  audible "MCP Test" track). `scripts/smoke_test.py` is the 2-second liveness
  check.
