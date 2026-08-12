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

- Device tools reach regular tracks only: **master and return chains are
  unaddressable** (no limiter-on-Main via bridge; ask the user to drag one).
- Envelopes: **session clips only** (arrangement clips can't hold clip
  automation — Live API limit); unwarped audio clips are rejected (warp first).
- No scene rename, no track reorder, no track grouping, no arrangement clip
  move/resize (delete + re-place is the workaround).
- Arrangement clip indices are positional and go stale on any change —
  re-read `get_arrangement` before touching them.
- `load_item` on a `.mid` file ignores `track_index` (known bug — it lands on
  a new track at the end).
- Pitch convention: **C3 = 60** (Ableton), everywhere.
- `get_audio_levels` needs the optional M4L tap on the Main track; max 10 s
  per sample. Without it, `get_track_meters` still answers "is it sounding?".
- One MCP client at a time, serially, by design
  (`scripts/toggle_desktop_client.py` flips Claude Desktop's registration).
