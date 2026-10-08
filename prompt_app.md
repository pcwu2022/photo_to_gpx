# Prompt for the AI coding agent

Copy everything below the line into your AI coding tool (Claude Code, etc.) from the root of the repository that contains the existing Python CLI and `APP_SPEC.md`.

---

You are building **Photo Travel Video Studio**, a desktop app that productizes the existing Python CLI in this repository.

## Inputs
1. `APP_SPEC.md`: the authoritative product and architecture spec. Read it fully before doing anything.
2. The existing CLI code (`photos_to_video.py`, `travel_route.py`, `video_map.py`, `playback.py`, `photo_markers.py`, `vehicle_config.py`, `test_video.py`, and the README describing behavior). This is the reference implementation. Its behavior (timing, camera, pauses, markers, vehicle ranges, validation rules) must be preserved.

## How to work
1. **Start by planning, not coding.** Produce `PLAN.md` containing: your understanding of the spec, a repo layout, the sidecar JSON protocol (commands, events, error shapes), the Route Edit Model schema, risks, and a task list per milestone (M0 to M5). Flag any spec ambiguity as a numbered question and propose a default. Then proceed with the defaults unless the answer is blocking.
2. **Work one milestone at a time** in the order M0 → M5. At the end of each milestone: all tests pass, the app runs, and you summarize what works, what is stubbed, and what is next. Do not start the next milestone until the current one meets its acceptance checks.
3. **Keep the CLI working.** Refactor into a `core/` library with thin CLI and sidecar entry points. Run the existing unit tests (`python -m unittest test_video -v`) after every refactor step. Add a golden-frame regression test comparing sidecar output against CLI output for a small fixed demo.
4. **Stack (unless the plan shows a strong reason to change; if so, explain):** Tauri 2 (Rust shell) + React + TypeScript + Vite for UI; MapLibre GL for the interactive editing map; the Python pipeline as a PyInstaller-frozen sidecar over stdio JSON lines; Zustand or Redux Toolkit for state with an undoable command stack; Vitest + Playwright for UI tests; pytest/unittest for Python.
5. **Do not reimplement domain logic in the UI.** Routing, timing, validation, camera, and rendering live in the sidecar. The UI edits the Route Edit Model and asks the sidecar to recompute and preview.
6. **Keep processing local.** No network calls except map tiles and update checks. Provide an Offline mode that blocks them. Never upload photos or Timeline data.
7. **Handle the licensing items in spec section 8 explicitly.** Implement the tile provider as an interface. Do not make the public Esri or anonymous CARTO endpoint the production default; use a clearly marked development-only default and leave a documented configuration point for the production provider and required attribution.
8. **Verify Google Timeline formats before implementing the parser.** Formats have changed. Use sample fixtures you create for each variant listed in the spec, write parser tests, and fail with a friendly, instructive message on unknown formats. Do not guess field names silently: where unsure, document the assumption in `docs/timeline-formats.md`.
9. **Quality bar:** typed code, small modules, no dead code, meaningful error messages shown in the UI in plain language, cancellable long jobs with progress, accessible controls (keyboard + labels), light/dark themes. Every user-visible string goes in a resource file.
10. **Testing:** unit tests for the Route Edit Model operations (especially "apply mode to all legs after node N", range edits, undo/redo), photo status classification, Timeline parsing/path substitution, and project save/load. Integration test: import a folder of generated EXIF-bearing JPEGs → edit modes → render a 3 s video offline with the plain map → assert file exists, frame count, and the vehicle icon switches at the expected time.

## Milestone definitions
Use the milestones M0 to M5 and the acceptance criteria in `APP_SPEC.md` sections 9 and 10.

- **M0**: refactor to `core`, protocol, sidecar packaged for the current OS, Tauri hello-world round trip.
- **M1**: import/scan/thumbnails/geo status/filter/selection/project save-load.
- **M2**: route editor with nodes/legs, per-leg and "all after node" mode changes, undo/redo, CLI-compatible ranges export.
- **M3**: sidecar frame preview + scrubber, style panel, render with progress/cancel, friendly report.
- **M4**: GPX + Google Timeline import, path substitution, activity→mode prefill, privacy trimming.
- **M5**: tile-provider integration, title/end cards, music, presets, installers, signing/update scaffolding, accessibility pass, sample project.

## First actions
1. Read `APP_SPEC.md` and the README of the CLI.
2. Inspect the code structure and list the public functions the sidecar will need.
3. Write `PLAN.md` and show it to me.
4. After I approve (or if I say "proceed"), begin M0.

Ask me at most three questions at the start, only if they are truly blocking. Otherwise state your assumptions and continue.