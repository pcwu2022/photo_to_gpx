# Desktop app roadmap: Photo Travel Video Studio

**Status: proposed future application, not implemented in this repository.** The current product is the local Python video CLI documented in the [README](../README.md). This document describes future scope rather than available features.

A consumer desktop app that turns a folder of geotagged photos (optionally enriched with Google Timeline data) into a shareable animated travel-map video. It is the productized successor to the existing Python CLI (`photo_to_gpx/video/photos_to_video.py` and its modules), which remains the reference implementation for all route, timing, camera, and rendering behavior.

---

## 1. Goals and non-goals

### Goals
1. Anyone can make a travel video in under 5 minutes with no command line.
2. All processing is local. Photos never leave the device.
3. Users can correct the route after loading it: change transport mode for any leg or for everything after a chosen node.
4. Visual output stays faithful to the CLI: proportional time playback, rounded local turns, flying camera, persistent circular photo markers, final overview.
5. The video is customizable (look, speed, resolution, vehicles, map style).

### Non-goals (v1)
- Cloud accounts, hosted rendering, or social feed.
- Reconstructing real roads/railways (still straight/geodesic/rounded connections).
- Video editing beyond this one template (no timeline of arbitrary clips, no audio editing beyond one music track).
- Mobile apps (see roadmap).

---

## 2. Platform decision

**v1: Desktop app (Windows, macOS; Linux best effort) using Tauri 2 + React/TypeScript UI, with the existing Python pipeline packaged as a sidecar process.**

Rationale:
- Users explicitly want it to work on their own folders. Desktop has the best folder/file access, and many travelers export phone photos to a computer.
- Maximum reuse: the tested Python pipeline (Pillow, PyProj, imageio-ffmpeg) ships as a frozen sidecar (PyInstaller/Nuitka), so there is no rewrite risk for v1.
- Hardware H.264 encoders (NVENC/QSV/VideoToolbox) are already probed by the pipeline.
- Tauri gives small installers, native file dialogs, and OS-signed distribution.

Sidecar contract: the UI talks to the Python process via a local JSON-lines protocol over stdin/stdout (or a localhost socket bound to 127.0.0.1 with a random token). Commands: `scan`, `build_route`, `preview_frame`, `render`, `cancel`, `import_timeline`. Events: `progress`, `warning`, `done`, `error`.

**Roadmap (not v1):**
- v2: Port the renderer to TypeScript (Canvas/WebGL + WebCodecs + a muxer) so one codebase serves web (PWA with File System Access API) and mobile (Capacitor/Tauri mobile). Keep the Python tool as a golden-output test oracle.
- v3: Mobile with direct camera-roll access (where most photos actually live) and on-device Timeline export.

Alternatives considered: Electron (bigger, no advantage), pure web (no reliable folder access in Safari/Firefox, heavy encode), native mobile first (rendering 1080p/180 s on phones is feasible but a full rewrite).

---

## 3. Users and primary flows

**Persona:** a traveler with 50 to 2,000 photos from a trip who wants a fun recap video for family or social media.

**Core flow (wizard-like, but non-linear after step 2):**
1. **Import**: choose folder(s) or drag and drop. Optionally add Google Timeline file or GPX.
2. **Select photos**: grid selector, geo filter, deselect/darken.
3. **Route editor**: map plus leg list. Edit transport modes, fix problems.
4. **Style**: customize the look. Live preview scrubber.
5. **Export**: choose resolution/FPS/length, render, open/share.

Projects auto-save so users can leave and return.

---

## 4. Feature requirements

Priority: **P0** = v1 must, **P1** = v1 should, **P2** = later.

### 4.1 Import and photo selector
- P0: Pick one or more folders (recursive) or drag and drop. Supported: JPEG, PNG, HEIC/HEIF (bundle decoder; report unsupported formats instead of failing silently).
- P0: Thumbnail grid (virtualized, lazy-loaded, disk-cached thumbnails). Scans must not block the UI; show progress.
- P0: Each thumbnail shows status: has GPS + timestamp (normal), missing GPS (darkened with a "no location" badge), missing timestamp, unreadable, or rejected by validation (with reason from the pipeline: speed too high, same-time conflict, out of projection range).
- P0: Filter controls: *All / With location / Without location / Excluded*; sort by time or filename; date-range slider.
- P0: Selection: click, shift-range, select all visible, invert. Photos without geodata are deselected by default and cannot become route anchors, but can be shown in a "Not on map" side list.
- P0: Raw 1-based index (see CLI spec: sorted case-sensitive relative filename, before filtering) is stored per photo and shown on hover, so CLI-compatible ranges can be exported/imported.
- P1: Burst/duplicate collapsing (reuse the burst grouping rule: ≤30 s between shots, ≤50 m, ≤2 min overall) with an expand control; every member still gets a marker if selected.
- P1: Manual location assignment for photos without GPS by clicking the map, or by inheriting from the nearest-in-time photo (clearly marked as inferred).
- P2: Import from Photos library (macOS Photos, Windows Photos) and cloud albums.

### 4.2 Time zones
- P0: Honor per-photo EXIF offsets; otherwise a project-level fallback IANA timezone (default: system zone, with a prompt if photo times span multiple UTC offsets).
- P1: Warn when photos appear to span time zones but lack offsets.

### 4.3 Route editor (headline feature)
- P0: Map shows the loaded route as polyline with a **node** at each accepted photo anchor. Legs between nodes are selectable.
- P0: Leg list panel beside the map shows: from→to, distance, real elapsed time, inferred mode, current mode (icon), pause length.
- P0: **Change transport mode** by:
  - clicking one leg;
  - shift-clicking a range of legs;
  - **selecting a node and choosing "apply to all legs after this node"** (and "before this node");
  - selecting a time range or drag-selecting on the map.
  Modes: car, train, plane, plus bundled bus, bike, walk, boat, and custom north-facing PNG icons.
- P0: Mode changes show immediately in the map preview (icon on the leg, different line style). Undo/redo for every edit.
- P0: Mode is stored per leg (destination-node semantic, matching the CLI: each leg uses the icon for its destination photo). Export/import the equivalent raw-index ranges JSON for CLI compatibility.
- P1: Mode suggestions: propose a mode per leg from speed and distance (e.g. >150 km at high average speed → plane) as a one-click "apply suggestions"; never apply silently.
- P1: Per-mode behavior options: line style/color, default speed hint, and whether the leg uses a long-transfer geodesic (planes) or a rounded local curve.
- P1: Remove a node (excludes the photo from route but may keep its marker), merge nodes, drag a node to adjust the anchor (marker location stays tied to the photo unless the user also moves it).
- P1: Insert a **via point** on a leg to bend the route (e.g. along a coast). Route still passes through every photo anchor exactly.
- P2: Snap-to-road/rail routing via an optional offline routing engine.

### 4.4 Google Timeline and GPX import
- P0: Import GPX files (reuse `read_gpx_points()` logic).
- P0: Import Google Timeline exports. Note: Google moved Timeline to **on-device storage** and the export format has changed more than once; at implementation time, verify the current export format from the Timeline app (Android/iOS) and support: the new on-device `location-history.json` (`semanticSegments`, `rawSignals`, `timelinePath`, `visit`, `activity`), and the legacy Takeout `Records.json` / `Semantic Location History` formats. Detect format automatically and give a friendly error with instructions if the file is unrecognized.
- P0: Use Timeline to **improve movement between photo anchors**: between two consecutive photo nodes, replace the straight/geodesic connection with the recorded path (simplified and smoothed), while every photo anchor stays exact.
- P0: Use Timeline `activity` types (IN_PASSENGER_VEHICLE, IN_TRAIN, FLYING, WALKING, CYCLING, ...) to **pre-fill transport modes** per leg. The user can override anything.
- P0: Show Timeline path as a distinct overlay with a toggle ("Use Timeline detail" per leg or globally), and show confidence/coverage gaps.
- P0: Time-gap handling: when Timeline has no data for an interval, fall back to the photo-only connection and mark it "inferred".
- P1: Privacy filter: crop out Timeline data outside the photo date range by default; the user can choose a wider window. Never include unrelated personal locations (home/work) unless the path passes through them within the chosen window; offer a "trim near home" tool.
- P1: Timeline `visit` places can optionally create labeled stops (name shown on the map during pauses).
- P2: Merge multiple sources with a priority order (photos > Timeline > GPX).

### 4.5 Map and camera
- P0: Preserve all CLI behavior: flying camera, fixed, and follow modes; city-scale stillness; proportional elapsed-time mapping (`--timing time`) with compressed option; arrival pauses; rounded local turns; final overview with spread, non-overlapping photo circles.
- P0: Preview scrubber: users can drag through animation time and see the camera, vehicle, trail and markers at low resolution (frames rendered by the sidecar with `preview_frame`; cached tiles only).
- P0: Map tile providers behind an interface. See section 8 for licensing; **the public Esri endpoint used by the prototype must not be the default in a distributed product without verifying terms.**
- P1: Map styles: topographic, satellite, minimal light/dark, schematic (offline).
- P1: Pre-download tiles for the route with progress and a storage budget; Offline mode.
- P2: Language choice for labels.

### 4.6 Video customization
- P0: Resolution presets (1080p landscape default, 720p, 4K, vertical 1080×1920 and square 1080×1080 for social), FPS (24/30/60).
- P0: Total movement duration (default 180 s), arrival pauses (long/short), overview zoom-out and hold seconds. Show the **resulting final length** live.
- P0: Timing mode: proportional (default) or compressed with its parameters under "Advanced".
- P0: Vehicle: choose per mode, scale, optional custom icon.
- P0: Photo markers: size, rim color, timestamp on/off, enable/disable photos entirely.
- P0: Trail: color, width, glow, fade; line style by mode.
- P1: Title card and end card (text, date range, optional stats: distance, countries, photo count).
- P1: Date/time display style and position; unit system (km/mi).
- P1: Background music (one local audio file, fade out, trim to video length). No bundled copyrighted music; if bundled tracks are offered, they must be properly licensed.
- P1: Style presets/themes (save and share as JSON).
- P2: Photo pop-up mode (large image briefly on arrival) in addition to persistent circular markers.
- P2: Per-segment time emphasis (slow down/speed up a leg manually).

### 4.7 Export
- P0: H.264 MP4 with automatic hardware encoder probing and `libx264` fallback; progress bar, ETA, cancel; a failed render never destroys an existing output file.
- P0: Output file + `*.report.json` (frame count, encoder, skipped photos, warnings), plus a human-friendly summary shown in the UI (not raw JSON).
- P0: "Show in folder", copy path, system share where available.
- P1: Quick low-res draft export; GIF/short clip export for a chosen time range; H.265.
- P1: Render queue and background rendering while the user keeps editing a copy.

### 4.8 Projects and persistence
- P0: Project file (`.ptv.json` + project directory) storing source folders (paths/bookmarks, not copies of photos), selection, per-leg modes, route edits, style, and last export settings. Relink dialog when folders move.
- P0: Auto-save and crash recovery. Undo history persisted per session.
- P1: Import/export of CLI-compatible `vehicles.json` ranges and the photo-index manifest.

### 4.9 Accessibility, localization, polish
- P0: Keyboard navigation for grid and leg list; color-contrast compliant; screen-reader labels for controls.
- P0: Light/dark UI. English strings in a resource file (i18n-ready).
- P1: Additional languages.

---

## 5. Architecture

```
┌─────────────────────────── Tauri shell (Rust) ───────────────────────────┐
│  File dialogs · permissions · project storage · updater · sidecar mgmt   │
│        ┌───────────────── React + TypeScript UI ─────────────────┐       │
│        │ Photo grid · Map editor (MapLibre GL) · Leg list ·       │       │
│        │ Style panel · Preview scrubber · Export dialog           │       │
│        └───────────────┬──────────────────────────────────────────┘       │
│                        │ commands/events (JSON)                           │
│        ┌───────────────▼──────────────────────────────────────────┐       │
│        │ Python sidecar: existing modules                         │       │
│        │ travel_route · video_map · playback · photo_markers ·    │       │
│        │ vehicle_config · timeline_import (new) · FFmpeg          │       │
│        └──────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────┘
```

Key decisions:
- **Interactive editing map is MapLibre GL in the UI**; the **render map stays the Pillow tile compositor** in the sidecar so exported video matches the CLI. The editor map is a visualization of route data and need not be pixel-identical; the preview scrubber (sidecar frames) is the source of truth for the look.
- The sidecar owns domain logic. The UI never reimplements routing, timing, or validation; it edits a **Route Edit Model** and asks the sidecar to recompute.
- Long operations are cancellable jobs with progress events.
- Refactor the CLI into a library (`core/`) plus thin CLI and sidecar entry points. The CLI keeps working and serves as regression oracle.

### Route Edit Model (UI ↔ sidecar)
```json
{
  "version": 1,
  "nodes": [{"id": "n1", "photo_id": "p1", "lat": 0, "lon": 0, "utc": "...", "inferred": false}],
  "legs": [{
    "id": "l1", "from": "n1", "to": "n2",
    "mode": "train", "mode_source": "user|timeline|suggested|default",
    "path_source": "geodesic|local_curve|timeline|via_points",
    "via": [], "pause_override_s": null
  }],
  "style": {},
  "timing": {"mode": "time", "duration_s": 180}
}
```
Edits are pure functions on this model, enabling undo/redo and project save.

---

## 6. Performance and quality targets
- Scan 2,000 photos: grid usable within 10 s on a typical laptop (progressive thumbnails).
- Preview frame (cached tiles): < 300 ms.
- Render 180 s 1080p30 with hardware encoder: ≤ 10 minutes on a mid-range laptop; no network access during encoding (tiles pre-cached).
- Memory: never hold full-video frames; stream one frame at a time as the CLI does.
- Deterministic output: same project + same version = same frames (for tests).
- Profile before optimizing, as noted in the CLI documentation.

---

## 7. Privacy and security
- All processing local; no telemetry by default. Optional opt-in anonymous crash reports.
- The only network traffic: map tiles (and update checks). Show this clearly in the UI and in Offline mode, block all of it.
- Photo EXIF includes precise home locations: exports contain only what the user selected. Report JSON paths can be redacted ("share-safe report").
- Timeline import: parse locally, keep only the window needed, don't write the raw file elsewhere; offer "delete cached Timeline data."
- Sidecar listens on no network port (stdio), or on 127.0.0.1 with a random token.
- Code-sign and notarize installers; auto-update over HTTPS with signature verification.

---

## 8. Licensing and legal checklist (must resolve before public release)
- **Map tiles:** the public Esri World Topo endpoint and CARTO's anonymous endpoint have usage terms that may not permit distribution in a commercial or widely distributed app. Choose a provider with an appropriate plan (e.g., MapTiler, Stadia, Mapbox, Thunderforest) or self-host Protomaps/OpenStreetMap vector/raster tiles; implement provider selection, API-key handling (user-supplied key or app-managed proxy), caching rules and **required attribution** in every video.
- Third-party code: upstream MIT notice in `THIRD_PARTY_LICENSE.txt` must ship in "About/Licenses". Audit all dependencies (Pillow, PyProj, FFmpeg build licenses; use an LGPL-compliant FFmpeg build; confirm H.264 and HEIC codec patent/licensing implications).
- Bundled vehicle icons and music must be original or properly licensed.
- Google Timeline: user-provided data only; no use of Google APIs required.

---

## 9. Milestones

**M0: Foundations.** Refactor CLI into `core` library; define JSON protocol; sidecar packaging on macOS + Windows; Tauri shell with hello-world round trip.

**M1: Import and select.** Folder import, scan, thumbnails, geo status, filter, selection, project save/load.

**M2: Route and editor.** Route Edit Model, MapLibre map with nodes/legs, leg list, mode editing (single, range, "all after node"), undo/redo, CLI-compatible ranges export.

**M3: Preview and export.** Sidecar frame preview, scrubber, style panel (core options), render with progress/cancel, report summary.

**M4: Timeline and GPX.** Format detection, path substitution, activity→mode prefill, privacy trimming.

**M5: Polish and release.** Tile-provider integration and licensing, title/end cards, music, presets, installers, signing, updater, accessibility, onboarding sample project.

---

## 10. Acceptance criteria (v1)
1. A non-technical user can import a folder, change modes after a chosen node, and export an MP4 without opening a terminal.
2. Photos with no GPS are visibly darkened, filterable, and excluded from the route by default.
3. Changing mode after node N changes the icon for every subsequent leg in preview and in the exported video.
4. A Timeline import visibly changes routes between photos while all photo anchors stay exact, and prefilled modes can be overridden.
5. Exported video for a fixed reference photoset matches CLI output within visual tolerance (golden-frame comparison on selected frames).
6. Rendering works fully offline from cached tiles; a failed render never damages an existing output.
7. No photo data leaves the device; verified by network inspection in tests.
8. Installers are signed; app starts on a clean machine with no Python or FFmpeg preinstalled.

---

## 11. Open questions
- Which map provider/plan, and who pays for tiles (user key vs. app subscription)?
- Free vs paid model (watermark, resolution limit, length limit)?
- Is mobile (camera roll access) a near-term priority, justifying the TypeScript renderer port early?
- Which Timeline export variants must be supported on day one?
- Minimum OS versions and GPU assumptions?
