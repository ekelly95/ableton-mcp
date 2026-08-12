# Driving this bridge well — guidance for AI agents

You (an AI agent, any MCP client) are controlling a real Ableton Live session
that a human is watching. This file exists because two agents drove the same
bridge in Aug 2026 and one used a fraction of its surface: everything below is
either verified against real Live or learned from that comparison. Deeper
design rationale and Live API facts: [docs/architecture.md](docs/architecture.md).

## Ground rules

- **Bridge first, always.** Never fall back to screenshots, window automation,
  or synthetic keystrokes to reach something the bridge can't — the user has
  explicitly vetoed screen puppeteering (it's disruptive to watch, brittle, and
  this bridge exists to replace it). If the bridge can't do a thing, say so
  plainly and name the exact manual step you need from the user.
- Orient before acting: `get_session_overview` first, `get_bridge_status` when
  calls fail. Don't rebuild state you can read.
- The user hears everything live. Stop the transport when you're done playing;
  don't leave loops running while you think.

## Plug-ins and presets (2.4, verified on real Live 12.4.3)

- Load third-party plug-ins via the `plugins` browser root:
  `browse ['plugins']` → format (`VST`/`VST3`) → vendor → loadable item, then
  `load_item` with `track_index`. Verified working for Purity (VST2) and
  Omnisphere (VST3). No more asking the user to drag plug-ins in.
- **Patch/preset selection inside a plugin stays human.** Verified: these
  plug-ins publish no presets to Live's browser (children empty), and a fresh
  PluginDevice exposes only "Device On" until the user clicks Configure. So:
  pick the patch *name* yourself (read the plugin's own library files on disk
  if you need the catalogue), then ask the user to click it — one line, e.g.
  "In Omnisphere, load 'Cedar Plucked Piano'".
- Native devices: `insert_device` by exact name, no browser round-trip.
- First `load_item` in a fresh Live session can take up to 120 s (indexing).

## Take lanes & deep device control (2.7, verified on real Live 12.4.3)

- **Variations without timeline clutter:** `create_take_lane` (name it), then
  `create_arrangement_clip`/`add_notes`/`transform_clip` with the SAME
  `take_lane_index` + `arrangement_clip_index` — indices count within the
  lane. Lanes and their clips show up in `get_arrangement`. **Lanes cannot be
  deleted, ever** (no API) — reuse before creating, cap is 8 per track, and a
  lane clip can't be deleted either (it dies with its track). The user
  auditions/comps lanes in Live's UI; you write into them.
- **Simpler, EQ Eight, Drift go deeper than their parameter lists:**
  `get_devices` with a device_index reports `class_properties` (Simpler's
  playback_mode/slicing modes/voices, EQ Eight's global_mode (Stereo|L/R|M/S)
  and oversample, Drift's voice mode/count and full mod matrix with Live's
  own choice labels) plus `class_methods`. Set them through
  set_device_parameters by NAME with the label ("One-Shot", "M/S") or
  true/false; run Simpler's sample ops via `invoke`:
  `[{method: "reverse"}]`, `[{method: "warp_as", beats: 4}]` — warp methods
  appear in class_methods only when their can_warp_* gate is true.
- **One instrument per chain:** `insert_device` refuses a second instrument
  on a track that already has one (Live's rule). To swap the sound, load the
  new sample/instrument with load_item (it replaces in place) or delete the
  old device first.

## Finding sounds (2.6, verified on real Live 12.4.3)

- **Search before you browse.** `search_library` reads Live's own library
  database directly (works even with Live closed): samples, presets/racks,
  MIDI files, grooves, Sets, plug-ins — by name substring, Live tags
  (comma-separated, all must match; mine the `tags` field of results for the
  vocabulary), kind, and source, sorted by how often the user actually used
  each item. `find_similar` ranks by sonic similarity to a reference sound
  using Live's own audio analysis — "more 808s like this one".
- **Loading a hit:** when a result carries `browser_path_guess`, pass it
  straight to `load_item` (verified: user-library sample → Simpler on the
  target track). Samples always also work via `import_audio` with the
  result's absolute `path` (forward slashes are fine). Core Library hits
  carry no browser path (Live's browser arranges them by category, not disk
  layout) — for those, search gave you the exact NAME, so load native
  presets via `insert_device`/browse by name, or ask the user.
- A `staleness` field on results means Live has database writes not yet
  visible to the search — very recent additions may be missing; everything
  else is current.

## Token economy (2.5) — how to not burn the user's usage

- **Write notes as notation, not JSON.** `add_notes` takes a `notation` string:
  stateful prefixes (`v100` velocity / `v90-110` range, `n/16` duration with
  `d`/`t` for dotted/triplet, `p0.9` probability), pitch names (`C3`=60, chords
  by juxtaposition), positions as `bar|beat` (`1|1`; a beat is a quarter note),
  repeats `1|1x16@n/16`, bar copies `@2=1` / `@3-8=1-2`, `v0` deletes.
  Measured: a 76-note 4-bar drum pattern is 52 chars of notation vs 4,850 of
  JSON — 99% less. Bad tokens warn-and-skip; check `notation_warnings`.
- **Read notes back with `format: "compact"`** when you just need to see the
  material (185 chars vs ~7,300 for the same clip). Use the default JSON only
  when you need `note_id`s for surgical `update_notes`/`remove_notes`.
- **Reshape existing clips with `transform_clip`** — the notes never enter the
  conversation at all: `'F#1: timing = swing(0.57); where(note.velocity > 100):
  v-15'`. Selectors (pitch / time / `where()`), assignments on velocity, pitch,
  timing, duration, probability, deviation, waveforms (`tri(1bar)`), `ramp`,
  `swing`, `quant`, `legato`, `snap(C,Eb,G)`, `rand`/`choose` (pass `seed` for
  reproducibility), and note ops `ratchet`/`repeat`/`merge`. The same language
  is available inline via `transforms` on `add_notes`.
- **Reads omit noise by default:** session overview skips empty clip slots and
  sends (flags bring them back); device/note fields at their defaults are
  simply absent — absent = default, never "unknown".

## The arrangement playbook (proven in production)

Work Session view → Arrangement, in this order:

1. **Foundations first:** `set_transport` — tempo, signature, key/scale.
   Everything composed later leans on the scale being right.
2. **Name and color every track** as you create it (`set_track`): role-prefixed
   names ("MAIN — Cedar Plucked Piano", "808 — Resolve") and a distinct
   `color_index` per role. The user reads the set in the UI; make it legible.
3. **Build section variants as Session clips:** compose the full loop, then
   `duplicate_clip` + `remove_notes` to thin copies into intro/verse/outro
   versions. Name clips by section: "HOOK — Cedar Full", "VERSE — 808 Sparse".
4. **Clip envelopes for dynamics** (`set_clip_envelope`, session clips only):
   volume rises into hooks, fades out of outros.
5. **Stamp the timeline:** `place_clip_in_arrangement` per your section map —
   a 3–4 minute song is ~60–80 placements, that's normal.
6. **Drop a locator at every section** (`create_locator`: "INTRO", "HOOK 1",
   "BRIDGE"…). Transport must be STOPPED. Locators are how the user navigates
   what you built.
7. **Revise by listening, not assuming:** `transport_control` play at each
   section boundary, sample `get_audio_levels` (≤10 s per call) or
   `get_track_meters`, then `get_arrangement` → `delete_arrangement_clip`
   (always pass `expected_start_time`) → re-place a different variant.
8. **Before the user exports:** make sure the Arrangement owns playback —
   if `back_to_arranger` is true, Session clips will render instead of the
   song. Clear it via `set_transport`.

## Hard limits (verified — don't rediscover these)

- Device tools reach master and return chains since 2.8: pass
  `track_type: "master"` (no track_index) or `"return"` to get_devices /
  set_device_parameters / insert_device / delete_device / load_item — a
  limiter or mastering plug-in on Main is one call now. One instrument per
  chain still applies everywhere.
- Envelopes: **session clips only** (arrangement clips can't hold clip
  automation — Live API limit); unwarped audio clips are rejected (warp first).
- No scene rename, no track reorder, no track grouping, no arrangement clip
  move/resize (delete + re-place is the workaround).
- Arrangement clip indices are positional and go stale on any change —
  re-read `get_arrangement` before touching them.
- `load_item` on a `.mid` file ignores `track_index` (known bug — it lands on
  a new track at the end; the result's `onto_track` names it). Recovery both
  agents used successfully: keep the auto-created track, delete your empty
  placeholder, rename. And DO load kit MIDI as the real file — never re-type
  a kit's notes from a disk parse; edit the imported clip in place instead.
- Pitch convention: **C3 = 60** (Ableton), everywhere.
- `get_audio_levels` needs the optional M4L tap on the Main track; max 10 s
  per sample. Without it, `get_track_meters` still answers "is it sounding?".
- One MCP client at a time, serially, by design
  (`scripts/toggle_desktop_client.py` flips Claude Desktop's registration).
