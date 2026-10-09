# Photo travel video

A local Python tool that turns timestamped GPS photos into an H.264 MP4 with a moving car, train, or plane, smooth flight camera transitions, rounded local turns, fixed arrival pauses, a ground-speed floor and zoom-dependent screen-speed ceiling, persistent circular photo markers, and a final overview of every accepted photo. Defaults: **1920×1080, 30 FPS**, **180 seconds of baseline motion**, retimed by speed limits, plus arrival pauses and a 10-second overview, elapsed-time-based playback, and the flying camera.

## Assessment and reuse

- `photos_to_timeline.py` already composes photo → GPX → Timeline JSON. Keep it for exporting to the existing Timeline Visualizer. The video pipeline avoids that intermediate conversion so photo paths, altitude, subsecond timestamps, and UTC offsets survive.
- `convert.py` provides the reused `dms_to_decimal()` conversion. Its existing extraction path falls back to filesystem timestamps, can miss nested `DateTimeOriginal`, and writes naive local time with a `Z` suffix. The video metadata reader fixes these issues without changing the existing scripts' behavior.
- `gpx_to_timeline.py` provides the reused `read_gpx_points()` and UTC timestamp parsing for the optional GPX video input.
- [mahlernim/google-timeline-visualizer](https://github.com/mahlernim/google-timeline-visualizer) now contains Kotlin, web, **and Python** implementations. Inspected `visualizer.py` at commit `16714b0b23b93c3ec6aeef45b7ebbe1e76329847`, particularly `get_map_image`, tile coordinate functions, `interpolate_latlon`, and `build_camera_track`. Adapted its bounded tile stitching, canonical longitude tile wrapping, and smoothstep easing. Its in-memory tile cache and per-frame map updates are replaced here by a persistent disk cache and an atlas built before encoding. The current camera combines eased flight transitions with viewport locking for short city movements. The upstream MIT notice is in `THIRD_PARTY_LICENSE.txt`.
- [trackanimation](https://pypi.org/project/trackanimation/) offers GPX plotting and video time normalization; its Matplotlib workflow is useful reference material, but it would still need the photo metadata, timing, camera, and compositor work.
- [motionpicture](https://github.com/Sbozzolo/motionpicture) orchestrates frame generation and FFmpeg assembly through movie recipes and saved frame files. Direct frame streaming is a better fit here.
- [MapAnim](https://github.com/ptarroso/MapAnim) animates raster maps; its GDAL-oriented raster workflow adds machinery this tile-based prototype does not need.

## Stack and data flow

Only **Pillow**, **PyProj**, and **imageio-ffmpeg** are required. No GeoPandas/Shapely/Matplotlib dependency is necessary for a sequence of geodesic segments and raster sprites. FFmpeg is supplied by imageio-ffmpeg's wheel on supported platforms; `IMAGEIO_FFMPEG_EXE` can select a system binary.

```mermaid
flowchart LR
    A[Photo folder] --> B[Pillow EXIF reader]
    G[Optional GPX] --> C[Sorted UTC records]
    B --> C
    C --> D[Validate points and map elapsed time to video time]
    D --> E[PyProj geodesic frame positions]
    E --> F[Projected route and camera bounds]
    T[Disk-cached raster tiles] --> H[Static tile atlas and route layer]
    F --> H
    H --> I[Pillow vehicle and trail compositor]
    E --> I
    P[Persistent photo markers] --> I
    I --> J[Raw RGB stream to FFmpeg]
    J --> K[H.264 MP4 and JSON report]
```

Modules: `travel_route.py` handles metadata and timing; `video_map.py` handles camera planning, maps and sprites; `playback.py` inserts arrival holds; `speed_control.py` retimes movements against ground distance and planned camera zoom; `photo_markers.py` handles image caching and circular markers; `vehicle_config.py` reads raw-photo icon ranges; `photos_to_video.py` connects them with a small argparse entry point.

## Interpolation and time compression

EXIF `DateTimeOriginal` is mandatory. Nested `OffsetTimeOriginal` and `SubSecTimeOriginal` are read; an explicit EXIF offset takes precedence over `--timezone`. Without an offset, the supplied IANA timezone is used (default UTC). Ambiguous or nonexistent daylight-saving wall times are skipped. Traveling across timezones requires correct per-photo EXIF offsets; a single fallback timezone cannot infer them. HEIC requires a separately installed/registered Pillow decoder; unsupported images are reported as skipped.

Records sort by UTC time, then path for deterministic ties. Coordinate ranges and DMS values are validated. Same-time photos within 50 m retain individual photo events but share one route anchor. Same-time locations farther apart are reported as conflicts and skipped. Segments exceeding `--max-speed-kmh` (CLI default 1,200) are rejected relative to the previous accepted anchor; raise this threshold for flights. This is a conservative plausibility check, not a complete outlier reconstruction algorithm.

For each accepted pair, use [PyProj `Geod.inv` and `Geod.fwd`](https://pyproj4.github.io/pyproj/stable/api/geod.html) for WGS84 ellipsoidal distance, position, and heading. Latitude/longitude are **not** linearly interpolated. Web Mercator is used only for map alignment, not ground distances. Longitudes unwrap for continuous antimeridian rendering. Locations/geodesics beyond the raster projection's ±85.0511° coverage are rejected.

Before speed limits, the default `--timing time` maps the entire accepted UTC interval to the chosen journey duration with one scale factor. A three-hour segment receives three times the video time of a one-hour segment, regardless of distance. Within each segment, the real timestamp advances linearly. On geodesic segments the distance fraction also advances linearly; rounded local curves are reparameterized by arc length. The speed controller then shortens or lengthens intervals when needed; intervals within the limits keep their original elapsed-time allocation. Original photo timestamps and route geometry remain intact.

`--duration 180` specifies the **baseline movement portion**, before speed limits or pauses; proportional mode defaults to 180 seconds. After every segment, freeze the vehicle and timestamp for a constant wait:

- **0.2 seconds** for distances at or above `--flight-km` (default 150 km), configurable with `--long-pause`.
- **0.05 seconds** for shorter distances, configurable with `--short-pause`.

Arrival pauses are separate from speed-controlled movement. Photo arrival times include earlier holds but trigger before the pause at their own location. Set both pause parameters to zero to disable arrival pauses. At 30 FPS, these continuous-time waits are quantized to available frames; short waits typically span one or two frames.

Very short real intervals can occupy less than one frame at a compressed frame rate. Photo arrivals round up to the next frame, and every accepted photo persists thereafter. Increase `--duration` to resolve short movements individually. At the end, the camera takes `--overview-seconds` (default 5) to zoom out and holds all photos for `--overview-hold` (default 5). `--popup-seconds` remains available as a minimum final overview hold (default 4); it does not expire markers.

The optional `--timing compressed` retains bounded elapsed-time compression:

```python
seconds = clamp((dt / time_scale) ** compression, min_segment, max_segment)
# defaults: time_scale=300, compression=0.5, min_segment=0.8, max_segment=6
```

This mode intentionally changes relative interval lengths to limit long gaps, but still uses elapsed time rather than distance to allocate segment durations. Its `--duration` override rescales segments and refuses segments shorter than 0.2 seconds. Min/max/compression settings are ignored by proportional mode.

`Route.at(animation_time)` returns animation seconds, UTC timestamp, latitude, longitude, heading, and segment index. Gaps above `--gap-hours` (default 12) are reported as inferred transfers. Connections do not reconstruct roads, railways, or actual activity during gaps. Local segments shorter than the flight threshold use cubic Bezier curves in an azimuthal equidistant metric projection, with tangent directions shared across adjacent segments. Control distances are bounded by `--turn-radius-m` (default 1,000 m) and 30% of neighboring segment lengths. Every photo anchor remains exact. A sampled arc-length table maintains time-based progress along each curve. Long transfers retain their WGS84 geodesics. `--turn-radius-m 0` disables local curves. The CLI's 1,200 km/h plausibility limit permits ordinary flights; `Route` itself retains the conservative 350 km/h default for callers that do not specify a limit.

## Animation speed limits

Defaults are **1,000 ground metres per animation second** (`--min-speed-mps 1000`) and **540 screen pixels per second** (`--max-screen-speed-px 540`). These are playback speeds, separate from the real-world `--max-speed-kmh` GPS plausibility check. The floor is independent of zoom: shorten a slow moving interval until its ground speed reaches the configured minimum. A same-location photo gap collapses to one frame of stationary motion rather than a long idle period. Arrival waits and the final overview remain separate.

The ceiling depends on the planned viewport: a pixel represents more ground distance at wider zoom, permitting faster planes while limiting vehicles in a local view. Calculate it using Web Mercator displacement, viewport size, camera translation, and zoom changes, with a small safety margin. The camera pulls back early on transfers outside the city threshold, rather than chasing a fast vehicle at street-level zoom. `--max-zoom-rate 1` additionally limits zoom changes to one doubling/halving per second. Local framing automatically widens if necessary to fit the requested floor and screen ceiling.

Limits can change the resulting video length; `--duration` is a baseline, not a fixed final runtime. Movement progress and photo triggers use the same piecewise clock, so photos still appear at their actual route anchors and fixed arrival waits stay unchanged. Set all three controls to zero to restore the original proportional clock and camera behavior:

```bash
--min-speed-mps 0 --max-screen-speed-px 0 --max-zoom-rate 0
```

If explicitly configured limits still conflict, the screen/zoom ceiling takes priority and the report counts the affected intervals. The default supplied `input/test1` run has no conflicts. Reports include per-leg nominal/adjusted durations and ground speeds, plus frame-level measured ground speed, screen speed, and zoom rate. Limits apply during the journey; the separate final overview retains its configured transition duration.

## Caching and rendering

The default `--camera fly` plans every camera viewport before encoding. It follows the journey, pulls back early for transfers outside `--city-km` when speed controls are enabled, and zooms into the destination. Without speed controls, the flight threshold is `--flight-km` (default 150). A sine-squared zoom envelope with earlier pullback on fast trips and eased camera centers follow departures and arrivals; the viewport widens if necessary to keep the vehicle visible. For segments within `--city-km` (default 5), the camera stays exactly still while the vehicle remains inside a safe viewport margin. If the vehicle reaches the edge, the camera pans at the current zoom. `--local-span-km` (default 25) controls local framing in projected map units. Camera planning remains independent of photo layout and vehicle timing.

Compute the union of required tiles across all camera zoom levels, then download/cache it before rendering. `--max-tiles` defaults to 1,200; when necessary, lower map zoom levels to stay within that budget. Requests use four workers. Rendering uses only cached files, with an LRU holding 128 tiles and four stitched viewport atlases; no network requests occur during encoding. Background atlases are rebuilt only when tile bounds or zoom level change. The route and trail use an independent curve sampling layer, so short turns remain rounded even when their photo times fall between video frames. Both the vehicle and the trail follow the same curves.

`--camera fixed` frames the complete route; `--camera follow` uses fixed zoom and keeps the vehicle centered under speed control; with speed controls disabled, it uses exponential center smoothing. Both share the cached tile renderer and final overview. Only the traveled red trail is drawn; future route segments remain hidden.

The default provider is the public [Esri World Topographic Map service](https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer). `--map-provider carto` requires `CARTO_BASEMAP_API_KEY` for uncached tiles; [CARTO requires an API key](https://www.carto.com/basemaps/apikey/) and its anonymous endpoint returns watermarked PNGs. Cache paths use separate provider namespaces under `.cache/photo-video/`. `--offline` refuses missing/corrupt tiles, while `--plain-map` provides a schematic background without geographic tiles. Full attribution is cached into a wrapped footer and remains visible at smaller resolutions.

Vehicle sprites are transparent, rotated clockwise according to projected heading, and cached by integer-degree rotation. Custom icons must face north/up. No full video frames are retained or written as PNGs: one RGB frame at a time streams to FFmpeg. An unavoidable frame copy and RGB byte conversion remain; Phase 3 should profile these before changing the architecture.

Automatic encoding probes actual one-frame H.264 encodes with NVENC, QSV, and VideoToolbox, then falls back to `libx264`. An explicit `--encoder` also falls back if its probe fails. Device availability depends on the FFmpeg build and host. A failed full encode leaves any existing destination video intact.

## Photo markers

Each accepted photo appears in a **circular, center-cropped thumbnail** with a white rim, soft shadow, and small timestamp. The circle center is its exact EXIF geographic location. Images honor EXIF orientation. Every marker stays available for the rest of the video, and multiple images coexist. A marker may move outside the current viewport as the camera travels; it returns when its location comes back into view.

Photo sizes are fixed in projected map units. At the closest camera zoom anywhere in the video, each circle has a diameter of **one-tenth of the screen width** (192 pixels at 1920×1080). Zooming out by a factor of two halves the circle diameter; its shadow and label scale with it. Photo centers, sizes, and the background use the same camera transform throughout the journey and final overview. There are no screen offsets or final-layout rearrangements. The vehicle draws last.

All photos remain registered at their locations. At wide zoom levels, thumbnails can become subpixel-sized and nearby photos can overlap; rasterization uses at least one pixel without moving the geographic anchor. The overview preserves the same geographical placement and map scale rather than promising individually readable thumbnails at global zoom. Reports include each marker's latitude/longitude, projected diameter, reference camera width, and overview pixel diameter.

Nearby burst grouping remains in the report (≤30 seconds between consecutive shots, ≤50 m from the cluster anchor, ≤2 minutes overall), but every member receives its own marker. A sprite cache accommodates the persistent marker count. `--no-photos` disables overlays. `photo_display` records raw index, arrival, visible seconds, and full-opacity seconds; expiry is null. `overlay_issues` records decode failures. `arrival_pauses` records every trip and its waiting time. Photos without usable timestamp/GPS data cannot be placed on the map and remain listed in the raw index manifest.

## Vehicle configuration

Use `--config vehicles.example.json` to select icons by raw photo index:

```json
{
  "default_icon": "car",
  "icons": {},
  "ranges": [
    {"start": 3, "end": 5, "icon": "train"},
    {"start": 5, "end": 10, "icon": "plane"}
  ]
}
```

Indices are **1-based and inclusive**, assigned to all supported image files in the directory recursively, sorted by case-sensitive relative filename, **before metadata filtering or chronological sorting**. Images missing GPS or timestamps still count. Non-image files do not count. Later rules win when ranges overlap, so index 5 above selects `plane`. Unspecified indices use `default_icon`, or `--vehicle` if the JSON omits it (default `car`).

Each travel leg uses the icon selected for its destination photo's raw index; at a photo arrival and its pause, that photo's icon is used. Available built-ins are `car`, `train`, and `plane`. For a custom north-facing transparent image, add an entry such as `"icons": {"boat": "icons/boat.png"}` and use `"icon": "boat"` in a range. Image paths resolve relative to the JSON file.

The generated `*.photo-index.json` lists every raw index, relative filename, selected icon, whether it became a route anchor/photo event, and any skip reason. Consult this manifest when editing ranges. `vehicles.example.json` illustrates the requested ranges; its assignments do not infer actual transport modes from the photos.

## Run

```bash
.venv/bin/python -m pip install -r requirements-video.txt

# Real photos: specify the timezone used by photos that lack UTC offsets.
.venv/bin/python photos_to_video.py --photos /path/to/photos \
  --timezone America/New_York --duration 180 -o travel.mp4

# Synthetic route demo (no photo files, so no photo markers).
.venv/bin/python photos_to_video.py --demo --duration 6 -o sample/phase1-demo.mp4

# Same demo using only the warmed disk cache.
.venv/bin/python photos_to_video.py --demo --duration 6 --offline -o /tmp/demo-offline.mp4

# Fully offline schematic, following camera, train vehicle.
.venv/bin/python photos_to_video.py --demo --plain-map --camera follow \
  --vehicle train --width 1280 --height 720 -o train.mp4

# Existing GPX and custom north-facing transparent icon.
.venv/bin/python photos_to_video.py --gpx trip.gpx --icon car.png -o trip.mp4

# Updated test1 video: persistent map photos and raw-index icon ranges.
.venv/bin/python photos_to_video.py --photos input/test1 --duration 180 \
  --config vehicles.example.json --min-speed-mps 1000 --max-screen-speed-px 540 \
  -o sample/test1.mp4

.venv/bin/python photos_to_video.py --help
.venv/bin/python -m unittest test_video -v
```

The adjacent `*.report.json` records frame count, encoder, rendering elapsed time, skipped-photo reasons, long-gap warnings, and each retained photo's animation time, UTC timestamp, coordinates, altitude, and path. Invalid input that prevents rendering is explained on stderr.

`sample/test1.mp4` is the latest render of the supplied `input/test1` photos, with geographically fixed, map-scaled markers and the 540-pixel/second speed ceiling. The adjacent report and raw-photo index describe the render. Local photos, generated videos, and reports are excluded from Git. To create a synthetic demo, use the `--demo` command above.
