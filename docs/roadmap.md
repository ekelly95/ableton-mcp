# Development record and remaining work

This document summarizes completed feature rounds, verification evidence, and
remaining work. Detailed implementation decisions and verified Live API facts
belong in [architecture.md](architecture.md); agent operating instructions
belong in [AGENTS.md](../AGENTS.md).

## Project constraints

1. **Keep the MIT implementation independent.** Producer Pal is GPL-3.0. Do
   not copy its implementation code, structure, or naming. Features are
   implemented independently, working from Ableton's documented interfaces and
   from behavior verified locally against Live.
2. **Keep pure computation outside Live.** Notation, transforms, library
   search, and other server-only work belong in `mcp_server/`. Only code that
   needs Live state belongs in `control_surface/`.
3. **Batch control-surface schema changes.** Every registry change moves the
   schema hash and requires reinstalling the control surface and restarting
   Live. Group related changes into one release.
4. **Verify before documenting support.** Mock tests establish repeatability;
   new Live API behavior also requires a real-Live probe or checkpoint. Update
   the tests and relevant documentation in the same change.

## Completed feature rounds

This record starts at 2.5. Earlier rounds — arrangement editing, clip
envelopes, the plug-in browser root, core metering — predate it and are
documented in the README and [architecture.md](architecture.md).

### 2.5 — Compact notation and transforms

- `add_notes` accepts a compact bar-and-beat notation as an alternative to raw
  note JSON.
- `transform_clip` applies selectors, timing and velocity changes, pitch
  operations, repeat/ratchet operations, and deterministic randomization
  without sending every note through the model.
- Compact output and omission of default-valued fields substantially reduce
  MCP response size. Measurements and format rules are recorded in
  [architecture.md](architecture.md#token-economy-25).

### 2.6 — Library search, sonic similarity, and macOS support

- `search_library` reads Live's library databases in read-only mode and
  searches samples, presets, MIDI files, grooves, sets, and plug-ins by name,
  tag, kind, and source.
- `find_similar` ranks analyzed audio files using Live's stored feature
  vectors (since 2.11, `search_library`'s `similar_to` option).
- Browser-path guesses are returned only where the disk-to-browser mapping has
  been verified. Absolute paths remain available for `import_audio`.
- The implementation is server-only and works while Live is closed.
- The client and control surface use an AF_UNIX socket on macOS and TCP on
  Windows.
- The installer recognizes the User Library and Ableton application-bundle
  locations used on macOS.
- CI covers Windows and macOS on Python 3.11 and 3.12.
- A 42-step checkpoint passed on 2026-08-12 against Live 12.4.3 Trial on an
  Apple Silicon Mac mini M2 Pro running macOS Tahoe 26.6.1.
- The optional Max for Live tap passed its 5-step protocol and calibration
  checkpoint on the same machine.

The Mac verification covered the Unix-socket transport and real Live behavior.
It did not include a full Claude Desktop session, long-running stability, or
the privacy prompts that may appear on a normally configured personal Mac.
The stdio launch and handshake paths are covered by the automated suite.

### 2.7 — Take lanes and native-device controls

- Arrangement MIDI clips can be created and edited in take lanes, and
  `get_arrangement` reports lane names and clips.
- Simpler, EQ Eight, and Drift expose selected class-level properties that are
  absent from their ordinary parameter lists.
- Simpler sample operations use the same device tool through guarded `invoke`
  actions.
- The 41-step real-Live checkpoint verified the feature round; the following
  2.8 checkpoint covered it again.

### 2.8 — Main and return device addressing

- Device reads, parameter writes, insertion, deletion, and browser loading can
  target ordinary tracks, return tracks, or the Main track.
- The 42-step real-Live checkpoint verified native-device insertion on Main
  and read/write behavior on a return chain.

### 2.8.1 — Local-boundary hardening

A security pass over the shipped surface. No schema change (the tool surface
and its hash are untouched), but the control surface itself changed, so this
needs a reinstall and a Live restart like any other round — the version bump is
what makes `get_bridge_status` say so.

- Windows TCP listener uses `SO_EXCLUSIVEADDRUSE`. Under `SO_REUSEADDR`,
  Windows lets any other process bind the same port and take over new
  connections (measured on Windows 11: the second bind succeeds). A hijacker
  would see every command and choose the replies, and replies land in an AI
  agent's context. Measured too: the exclusive option rebinds immediately after
  a full accept/teardown cycle, so script reload is unaffected.
- Unix socket is 0600, not 0660. Every ordinary macOS account is in `staff`.
- Logs moved off world-writable `/tmp` to `~/Library/Logs/AbletonMCP` (macOS),
  created 0700. The operations journal is a full record of a session, and
  anything on the machine could previously read it — or pre-create the
  directory with symlinks and have Live append wherever it liked.
- `NaN`/`Infinity` refused by parameter validation. Python's JSON parser
  accepts those literals, and NaN then passes every range check, since
  comparisons against it are all false — a NaN tempo or note position reached
  Live's API unchallenged. The check recurses into object params, which reach
  handlers unvalidated.
- A request `id` that cannot be a dict key no longer hangs up the connection.
- Frames that are well-formed JSON but not the protocol are answered rather
  than crashed on, in both directions. A request body of `null` parsed to None,
  which the connection handler reads as end-of-stream, so the peer was hung up
  on with no answer; other scalars produced a bare AttributeError. And a
  *reply* that was empty or foreign became `CommandError` — the class meaning
  "Live executed this and refused it" — which told the model a destructive
  write had reached Live and been rejected when nothing ever arrived. Both now
  point at the likely cause: something else on the port.
- `repeat()`/`ratchet()` counts are bounded by the read limit. One hallucinated
  digit used to ask the server process for hundreds of millions of note dicts.
- The trust model is now stated in the README and architecture document, not
  only in the Max for Live lab notes. `AGENTS.md` says that names and paths
  read out of Live are untrusted text, not instructions.

Deliberately not done: a shared secret between the two halves. It would have to
live in a file readable by anything running as the same user, so it stops other
*accounts* and nothing else, at the cost of an install step and a new way for
the halves to disagree.

### 2.9.0 — Fixes from re-verifying on Live 12.4.6

An audit, then each finding checked against real Live 12.4.6 (in a Windows 11
VM) before and after the fix. Every fix has a test that fails on the old code;
the mock now models the Live behaviour behind each one.

- **Clips whose loop doesn't start at 0.** `clip.length` is the loop length,
  but note and envelope times count from the clip's zero. A clip looped over
  4-8 had its notes read as 0-4 — the notes outside the loop — and
  `transform_clip` edited that wrong set. Reads, envelopes and `ramp()` now use
  the clip's real time range; `get_notes` also returns `clip_start`.
- **Several MCP clients at once.** The control surface served one connection at
  a time, so a second client (another session, a stale server process) timed
  out on every call. Each client now gets its own thread; commands still run
  one at a time. `scripts/toggle_desktop_client.py` is gone.
- **No more dropped commands.** _Framework's `schedule_message` list could lose
  a task appended from the socket thread, which then timed out after ~32 s.
  Tasks go through our own thread-safe queue, drained in `update_display`.
- **`transform_clip` is all-or-nothing.** Its remove/update/add writes are one
  internal `apply_note_diff` command that refuses if the clip changed since the
  read and validates everything before writing.
- **`update_notes` range-checks** every value, and checks all of them before
  changing any note.
- **`create_locator` no longer deletes locators.** Live snaps new locators to
  the grid, and a request within ~0.1 beat of an existing one toggled it off.
  The locator is now restored (with its name) and the request refused.
- **`place_clip_in_arrangement`** returns the real clip index, not -1.
- **`transport_control(stop, position)`** keeps the playhead where it was sent;
  a second stop had sent it back to the start.
- `ABLETON_MCP_HOST`/`ABLETON_MCP_PORT` (and the tap equivalents) point the MCP
  server at a Live on another machine, e.g. a VM through an SSH tunnel. Live
  itself still listens on loopback only.

### 2.10.0 — mcp 2 SDK

The server moves from mcp 1.x to mcp 2.3. The tools and their schemas are
unchanged; reinstall the package and the control surface, and restart Live.

- One server answers both protocol eras: 2026-07-28 clients and 2025-era
  clients that still initialize (Codex, Claude Desktop). Both verified over
  stdio against Live 12.4.6.
- Tool arguments are validated against their input schemas before dispatch —
  mcp 2 no longer does this, and the server-only tools have nothing else
  behind them.
- Errors still reach the model as readable `is_error` results ("Ableton
  rejected the command: …"); mcp 2 would otherwise reduce any raised error to
  "Internal server error".
- List results carry cache hints, since the tool list is fixed per process.
- CI installs from `uv.lock` and adds Linux and Python 3.13.

### 2.11.0 — Smaller tool definitions

46 → 43 tools, definitions 35.6k → 32.8k characters (~8.9k → ~8.2k tokens).
Measured first: input schemas are ~63% of the cost, so most of the saving is
shared-parameter text; tool count matters much less.

- `get_tracks` and `get_clips` are gone: `get_session_overview` returns the
  same data and gains `track_index` for a one-track read.
- `find_similar` is `search_library`'s `similar_to` option.
- The envelope tools address session slots only (`slot_index` required);
  arrangement addressing, which they could only refuse, is no longer offered.
- Shorter descriptions on the clip/track addressing parameters, and
  `update_notes` no longer repeats `add_notes`' field descriptions.

Writes and destructive tools are unchanged and separately named. Verified on
Live 12.4.6.

### 2.12.0 — Undo and redo

- Every write tool call runs inside one Live undo step, so a batch of 20
  parameter changes is one Ctrl+Z instead of 20. Exception, confirmed on Live
  12.4.6: Live records a rename as its own step even inside the group. A write
  that changes nothing undoable (a playhead seek) leaves no empty step.
- New `undo` tool: undo or redo up to 20 steps, reporting `can_undo` /
  `can_redo`. Live has one history shared with the user, so undo reverts
  whatever came last — the description tells the model to check first.

### 2.13.0 — Drum Rack pads and rack chains

- The device tools take `rack_index` + `chain` to work inside a rack: a Drum
  Rack pad by note ("C1" or 36), or another rack's chain by index. Equal-
  length lists reach nested racks, which Live's Core Library kits need (every
  pad holds an Instrument Rack around its Simpler).
- `get_devices` lists a Drum Rack's filled pads and other racks' chains.
- New `set_chain` tool: name, mute, solo, volume, pan for one pad or chain.
- Verified on Live 12.4.6 with the 505 Core Kit: 16 pads listed; set_chain;
  insert/delete inside a pad; Transpose on the kick's Simpler two racks deep.
- Not yet: loading a sample onto a pad. 45 tools.

### 2.14.0 — Parameters by display value

- `set_device_parameters` accepts the text Live displays ("-6 dB", "+5 st",
  "800 Hz", "2.5 kHz", "250 ms", "50 %", "30L", "4 : 1", "-inf dB") and choice
  labels ("High-pass"), alongside 0-1. Live has no set-from-string call, so
  the bridge bisects the parameter's range with `str_for_value` until the
  display matches — the same for linear and curved parameters.
- Verified on Live 12.4.6 across Auto Filter, Utility, Compressor and Reverb.
  Live quirks the mock now mirrors: `value_items` raises on continuous
  parameters; labels are hyphenated; ratios read "4.00 : 1".
- Seeking past the song's end now says so (and gives the song's length)
  instead of Live's "Cannot set the Songtime behind the Songlength".

### 2.15.0 — Samples onto Drum Rack pads

- New `load_sample` tool: an absolute audio file path onto a Drum Rack pad.
  Filled pads get the sample replaced in their Simpler (also inside the
  nested rack Live's Core Library kits use); empty pads are created with
  `RackDevice.insert_chain` + `DrumChain.in_note` + a Simpler and
  `replace_sample` — all found in Live 12.4.6's own LOM type table, no
  browser or selection involved.
- Verified on Live 12.4.6: nested replace in the 505 Core Kit, a new pad from
  a forward-slash path, missing-file error. Undo: replace is one step, a new
  pad two (Live commits the chain separately), a name one more. 46 tools.

### 2.16.0 — Hot reload

- `install_control_surface.py --reload` reloads command handlers in a running
  Live — no restart. (Since 2.20, `ableton-mcp-install` and the
  `install_control_surface` tool reload by default; `--no-reload` opts out.) An internal `reload_commands` command drops
  `commands/` and `utils/` from `sys.modules` and imports them fresh (no stale
  cross-module references); a failed import restores the previous modules and
  commands. The socket server, marshal, registry, errors and config are not
  reloaded, and the script says when a change touches them.
- Verified on Live 12.4.6: a changed handler went live with no restart, a
  syntax error was refused while the old handlers kept serving, and the
  original came back the same way.

### 2.17.0 — Measure the mix: analyze_audio and capture_audio

- `analyze_audio` (server-side, numpy): integrated loudness and loudness range
  (BS.1770-4 / EBU R128), 4x true peak, sample peak, RMS, crest factor,
  octave-band spectrum, stereo correlation and side/mid, for WAV and AIFF
  in every encoding Live writes. The EBU reference tone reads -23.0 LUFS.
- `capture_audio` records Live's master output through a temporary
  Resampling track and analyzes it — no Max for Live. Each Live-side step is
  its own command: creating, re-routing and deleting a track inside one task
  crashed Live 12.4.6 (access violation); one task apart, it is fine.
- Verified on Live 12.4.6: a built-in sample captured from the arrangement
  matched the source file's RMS, true peak and spectrum exactly (loudness +3
  LU, correctly, for a mono source on both channels). 48 tools.

### 2.18.0 — Refuse writes against a set that changed

- Index-addressed writes (and `undo`) are refused with `StaleSetError` when
  the set's structure changed since the caller's last read — the user, or
  another client, added, removed, renamed or moved tracks, devices, clips or
  scenes. Nothing is changed; the model re-reads and retries. Its own writes
  never trip it, a tweaked knob doesn't count, and tempo/transport/locator
  writes aren't checked. Automatic and wire-level: no new tool parameters.
  Verified on Live 12.4.6 with two clients: an insert at the top shifted the
  model's track 0, the write was refused, and the retry after a re-read hit
  the right track; an undo that would have reverted the other client's
  delete was refused.
- `capture_audio` deletes its recording (and Live's .asd sidecar) after
  analysis unless `keep_recording` — measuring a mix no longer leaves files
  in the project.

### 2.19.0 — inspect: a read-only escape hatch

- New `inspect` tool: any object in Live's API by canonical path (Max for
  Live's syntax), returning its readable properties and its children to walk
  into. Properties only — methods are never called — so it cannot change the
  set; writes stay with the validated tools. Two routing-channel properties
  are skipped as unverified suspects in a Live 12.4.6 crash.
- Verified on Live 12.4.6: song, application, views, tracks, mixer,
  parameters, master, scenes, clip slots, and a Core Library kit down to a
  pad's nested Simpler and its sample file path. 49 tools.

### 2.20.0 — One-click install

- MCP Bundle: `packaging/build_mcpb.py` builds `ableton-mcp-<version>.mcpb`
  (manifest 0.4, `uv` server type: the host installs the locked dependencies
  — numpy included — so no Python install is needed). The manifest is
  validated against the official schema and its tool list is generated from
  the server. Verified by launching the unzipped bundle exactly as a host
  does (`uv run --directory <bundle> server.py`).
- The installer moved into the package (`mcp_server/install.py`): new
  `install_control_surface` tool and `ableton-mcp-install` command copy the
  control surface that ships inside the package, so neither needs a repo
  checkout; on updates they hot-reload a running Live when they can.
  `get_bridge_status` points at the tool. 50 tools.
- Not yet verified: installing the bundle in Claude Desktop itself.

### 2.21.0 — Docs re-verified for release

- The docs' 12.4.3 claims were re-checked on 12.4.6 with silent probes; all
  held except one wrong assumption — `duplicate_clip` overwrote an occupied
  next slot (Live does that silently). It now refuses.
- Plug-ins: Purity (VST2) re-verified loading through the bridge.
  Omnisphere is now marked unverified: the 2.4 claim came from a session
  that drove it by screen automation. The plug-in tree is documented as
  machine-dependent, and patch choice as manual (save Live presets to make
  sounds loadable by name).
- README, AGENTS.md and the architecture notes tidied for the public release.

### 2.21.1 — Release polish

- Bundle manifest: tool summaries no longer cut off at "e.g." (three tools'
  listings ended mid-phrase).
- README: the Claude Desktop bundle install is described as built to the MCPB
  spec but not yet tested in Claude Desktop itself.
- First public release; the .mcpb is attached to the GitHub release.

## Remaining work

### Make the real-Live checkpoint self-contained

The checkpoint still expects one sample from a populated User Library. Replace
that dependency with a generated or bundled test sample so a clean Live
installation can run the complete checkpoint without copying library content.

### Simplify Max for Live tap packaging

The prebuilt `AbletonMCP Tap.amxd` works on Windows and macOS, but it is not
frozen, so its `tap_server.js` runtime must be installed beside it. The manual
build route already offers File → Freeze Device (README-lab step 6); shipping
a frozen prebuilt device would drop the two-file requirement.

### Gather first-user macOS evidence

A normal Mac installation may expose permission prompts or long-session issues
that were absent from the automated test machine. Record these as
they are reproduced; do not infer them from CI alone.

### Consider only when a real workflow requires it

- Event-level or curved clip envelopes. Exact signatures still require a
  real-Live probe.
- Additional native-device class-property tables.
- Meter-relative notation features or transform-language extensions.
- A later MCP SDK major-version upgrade after compatibility testing.
- Offline key, tempo, or drum analysis as a separate optional package
  (loudness and spectrum analysis shipped in 2.17 as `analyze_audio`).

## Design decisions that are not roadmap items

The following are intentionally excluded unless their constraints change:

- Generic tool consolidation without an evaluation harness capable of
  detecting tool-selection regressions.
- An unrestricted run-arbitrary-code tool inside Live.
- Bundled audio-generation or voice services.
- Arrangement automation lanes, which Live's exposed API does not provide.

## Verification checklist

For a release that changes behavior:

1. Run the full offline test suite and Ruff checks.
2. Verify installer and stdio-launch tests on both supported platforms.
3. Run `scripts/smoke_test.py` against Live.
4. Run `scripts/live_checkpoint.py` for Live-side changes.
5. Run `scripts/tap_checkpoint.py` for tap protocol or device changes.
6. Update README status, architecture facts, and AGENTS guidance from the
   observed results.
