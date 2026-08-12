# Roadmap: adopting producer-pal's good ideas, the bridge's way

Written 2026-08-12 for the next working session. ekelly95 has approved this
direction; the scope of any given round still gets confirmed with him before
building. Read AGENTS.md and docs/architecture.md before starting anything —
the conventions there are load-bearing.

## Why this exists

An Aug 2026 competitive study (producer-pal, ahujasid/ableton-mcp,
uisato/ableton-mcp-extended) identified features worth having and confirmed
this bridge's differentiators (automation, hearing, plugin loading, no-M4L,
MIT). The token round (2.5.0) already shipped the biggest lesson — notation +
transforms + compact emission. This file plans the rest.

## Ground rules (do not skip)

1. **No code from producer-pal.** It is GPL-3.0; this repo is MIT. Facts about
   Ableton (LOM paths, file formats, database schemas) are free to use and are
   recorded below so you never need to open their source. Feature *ideas* are
   fine; their code, structure, and naming are not. Where taste allows,
   diverge deliberately — we already did (quarter-note beats in notation,
   bar-diffing serializer they don't have).
2. **Adopt efficiently, not literally.** The goal is the *capability*, built
   the way THIS bridge works: registry-generated schemas, server-side
   processing wherever Live doesn't need to be involved, absent-=-default
   payloads, warn-and-skip errors, provenance-commented mocks.
3. **Placement rule** (from 2.5.0, it worked): anything that is pure
   computation lives in `mcp_server/` (iterate freely, no Live restart);
   only genuine Live-state access goes in `control_surface/` (reinstall +
   restart per release). Server-only tools (transform_clip pattern) sit
   outside the registry — no schema-hash impact.
4. **Batch schema changes into one release** — every registry edit moves the
   schema hash and costs ekelly95 a Live restart. One restart per round, ever.
5. **Verification is non-negotiable:** mock tests with provenance comments,
   then a real-Live probe before claiming anything works (the plugins-root
   round found the probe patterns; measure, don't assume). Update AGENTS.md
   and ekelly95's memory files with VERIFIED facts only, same session.
6. Loose end to clear opportunistically: `uv pip install -e ".[dev]"` in both
   venvs with all MCP clients closed (exe lock); full live_checkpoint.py
   re-run (pending since the 2.3.0 cleanup round).

## Round A — Library intelligence (top pick: highest value per effort)

**Goal:** the agent finds sounds/samples/presets/plugins by query, tag,
similarity, and usage — then loads them. Producer-pal can discover but not
instantiate; we already instantiate, so this combination beats them outright.

**The key fact (verified in their docs/source by the Aug 2026 study, and it
is a fact about Ableton, not their invention):** Live maintains SQLite
databases of the whole library at `%LOCALAPPDATA%\Ableton\Live Database\` —
`Live-files-*.db` (pick highest schema number in the filename, mtime as
tie-break) and `Live-plugins-*.db` (pick newest mtime; version numbers are
not monotonic). Open read-only + immutable (`file:...?mode=ro&immutable=1`)
so it is safe while Live runs; you read the last checkpointed snapshot, and
a `.db-wal` file newer than the `.db` means uncheckpointed writes you can't
see — surface that as a staleness note, not an error.

Schema facts to verify with your own probe before building on them:
- `files` (file_id, parent_id, name, use_count, file_type, subtype,
  place_id, device_type); `places.folder_kind` distinguishes user library /
  packs / builtin / sample folders. Full paths reconstruct by walking
  parent_id upward (one recursive CTE, root `X:` means Windows drive).
- `keywords` joins file_id to tag rows that live in `files` themselves.
- `fe_values` holds one row per analyzed file: `hash` (64-bit int — treat as
  an opaque TEXT equality key, never arithmetic) and `data`, a 268-byte BLOB:
  uint32 LE version=18, uint32 LE count=64, uint32 reserved, then 64 float32
  LE — Live's own audio feature vector. Cosine similarity over these is
  "find me sounds like this one". `struct.unpack_from("<III"/"<64f")` does it.
- Plugins DB: `plugins` table; format/category parse out of `dev_identifier`
  (e.g. `device:vst3:instr:Name`); `subcategories` is pipe-delimited.

**Build shape:** one server-side module (`mcp_server/library.py`, stdlib
`sqlite3` + `struct` only) + one or two server-only tools outside the
registry (`search_library`, maybe `find_similar`). No Live-side code at all,
no schema-hash movement, no restart. Filters: query substring, tags, kind,
source, sort by use_count. Cap results; absent-=-default fields. Loading the
found item goes through the EXISTING browse/load_item path — return each
hit's browser-path guess plus absolute file path, and verify the join between
DB paths and browser paths in the real-Live probe (this is the risky seam;
import_audio by absolute path is the fallback for samples).

**Verify:** probe script listing DB files, row counts, one path
reconstruction, one similarity ranking against a known 808 — then a live
search→load round trip. Mock tests with a fixture .db built in-test.

## Round B — Take lanes (small, self-contained)

**Goal:** stack MIDI variations on one arrangement track (audition takes
side by side without timeline clutter).

LOM facts (verify on real Live 12.4 before shipping): `Track.take_lanes`
excludes the main lane; `track.call("create_take_lane")` appends (there is
NO delete — lanes are permanent for the session, so create sparingly and
warn in the tool description); cap 8 non-main lanes; `TakeLane.name` is
settable; clips go in via `takeLane.call("create_midi_clip", start, length)`;
track-scoped clip APIs (duplicate_clip_to_arrangement etc.) silently no-op
on take-lane clips — everything must address the lane object.

**Build shape:** control_surface additions (this IS Live state):
extend `get_arrangement` with per-track lane summaries (absent when none),
add `create_take_lane` + lane addressing on `create_arrangement_clip`/
`add_notes` (a `take_lane_index` param mirroring the slot/arrangement XOR
pattern). Registry changes → batch with any other Live-side round. Mock
first with provenance comments; probe the no-delete and silent-no-op facts.

## Round C — Deep native-device control (data, not code)

**Goal:** reach the device state that never appears in `device.parameters`
(the reason a fresh plugin shows only "Device On" — but also native devices
hide topology switches there).

**Build shape — extend, don't multiply tools:** a mapping table in
`control_surface/commands/devices.py` keyed by `class_display_name`:
per device, named class-level LOM properties (int enums with label lists,
bools) and safe LOM methods. `get_devices` gains a `class_properties` block
when the table knows the device (absent otherwise); `set_device_parameters`
accepts those names transparently; device methods (e.g. Simpler's reverse/
crop/warp_as) ride the existing tools rather than new ones. ~85% of this is
table data; write it from the LOM docs and probe each entry on real Live.
Start with the devices ekelly95 actually uses: **Simpler, EQ Eight, Drift**
(sample ops; global_mode/oversample; mod-matrix `_index` properties + voice
mode/count). Add others only on demand. Registry changes → batch.

## Round M — macOS compatibility (ekelly95-approved 2026-08-12)

**Why:** the producer audience skews heavily Mac; Windows-only is the
bridge's single biggest reach limit, and both name-sharing competitors ship
macOS. Not a producer-pal adoption — our own gap.

**Already in place:** the control_surface script supports Unix sockets
(config.py: `SOCKET_PATH`, `USE_TCP = os.name == "nt"`); Python inside Live
is the same on both platforms. The gaps are client + installer + paths + CI.

Work items:
1. **Client:** teach `mcp_server/client.py` to connect over the Unix socket
   when not on Windows (mirror the control_surface's existing branch). This
   path has NEVER been exercised — treat it as new code, not dormant code.
2. **Installer:** `scripts/install_control_surface.py` learns the macOS
   Remote Scripts location (`~/Music/Ableton/User Library/Remote Scripts`,
   plus Live-version app-support fallbacks) alongside the existing Windows/
   OneDrive probing. Same copy-over-never-rmtree rule.
3. **Path assumptions sweep:** LOG_DIR (config.py branches on os.name —
   verify the non-Windows branch), samples dir, anything using %TEMP%/
   backslashes. Round A note: Live's database on macOS lives at
   `~/Library/Application Support/Ableton/Live Database` — build the library
   module with both paths from day one.
4. **CI:** add a macos-latest job to .github/workflows/tests.yml. The mock
   suite exercises the whole command surface without Live, so a green Mac
   run validates everything except the final real-Live handshake.
5. **README:** platform section updated to say exactly what macOS status is.

**Verification constraint (be honest about it):** ekelly95 has no Mac, so the
final real-Live handshake cannot be verified in-house. Ship as "implemented,
tests green on macOS CI, awaiting real-hardware confirmation" and say so in
the README — the first Mac user is the verifier, and their confirmation (or
bug report) gets folded back in. Do NOT claim macOS "support" as verified
fact anywhere (memory, README, AGENTS.md) until a real Mac + Live has run
the checkpoint. If a Mac becomes borrowable, scripts/smoke_test.py +
live_checkpoint.py are the 30-minute confirmation path.

## Round D — Only when the surface stabilizes

- **Tool consolidation (42+ → ~20).** The largest fixed cost (schemas ≈8-9k
  tokens/session) but touches everything. Pattern if/when done: one `delete`
  and one `duplicate` tool with a required `type` enum; comma-separated ids
  for batch ops; NEVER string|array unions (small models silently mangle
  anyOf — producer-pal proved this empirically). Do it in one dedicated
  release with AGENTS.md rewritten the same day.
- **Notation/transforms v2** from real usage: meter-relative beat option,
  serializer tuplet exactness, `next.*` / `seq()` / `split()` / arrangement-
  synced waveform phase in transforms — add when a session actually wants
  them, not before.
- **Master/return device addressing** — our own long-standing gap (device
  tools reach regular tracks only). Not producer-pal envy; fix when it hurts
  (the limiter-on-Main workflow already hurt once). resolve_track already
  understands "master"; the device tools' track resolution is the blocker.
- Emission format v2: JS-literal text (unquoted keys) is worth ~15-25% more
  than compact JSON on key-dense payloads. Cheap, server-only; measure first.

## What NOT to adopt

- **bar|beat meter-relative beats, chord symbols, notation dialect count** —
  we chose one dialect, quarter-note beats, deliberately (recorded in
  notation.py's docstring). Don't churn it.
- **Their M4L/device-embedded architecture, web chat UI, voice mode** —
  different product. The bridge's remote-script + server shape is settled.
- **ElevenLabs bundling** — anyone can run that server alongside; not ours.
- **Raw LOM escape-hatch tool** — already on the deliberately-absent list
  (crash risk inside Live; revisit only with a sandbox design).

## Suggested order

A (library, server-only, no restart) → M (macOS: code + CI now, real-Mac
verification when hardware appears — worth doing before any public flip,
since Mac users are most of the audience) → C (device tables) + B (take
lanes) batched as one Live-side release → D items on demand. A and M don't
conflict and could be one round. Confirm scope with ekelly95 at the start of
each round; he decides what a round is worth.
