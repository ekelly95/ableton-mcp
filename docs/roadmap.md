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
  vectors.
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
- Optional offline audio analysis as a separate package.

## Design decisions that are not roadmap items

The following are intentionally excluded unless their constraints change:

- Generic tool consolidation without an evaluation harness capable of
  detecting tool-selection regressions.
- An unrestricted run-arbitrary-code tool inside Live.
- Bundled audio-generation or voice services.
- Multiple simultaneous MCP clients; one serial client preserves ordering.
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
