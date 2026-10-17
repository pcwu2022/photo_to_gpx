# Original video implementation prompt

Historical design input; requirements and research below may be outdated. Use the [README](../../README.md) and [configuration reference](../configuration.md) for current behavior.

---

Act as a **senior Python developer specializing in geospatial visualization and video rendering**.

I am building a local Python tool that scans travel photos, extracts EXIF timestamps/GPS coordinates, reconstructs my route, and generates an animated MP4 showing a **car/train moving along the route**, with the actual travel photos appearing as pop-ups at the appropriate locations and times.

I have two starting assets:

1. My existing `photos_to_timeline` Python script.
2. Open-source code from `mahlernim/google-timeline-visualizer`.

**First inspect these existing implementations and reuse/adapt useful functionality instead of rewriting it.** Also check GitHub/PyPI for relevant existing solutions before implementing major components.

The attached images are **visual references only** for the desired travel-animation style.

### Core pipeline

```text
Photos
→ EXIF timestamp + GPS
→ chronological timeline
→ geospatial route
→ time-scaled interpolation
→ cached map background
→ moving vehicle
→ photo pop-ups
→ MP4
```

### Requirements

#### 1. Photo metadata

Extract:

* `DateTimeOriginal`
* latitude/longitude
* optional altitude
* image path

Handle missing/invalid metadata gracefully and report skipped photos.

#### 2. Route & interpolation

Connect photos chronologically and generate a smooth animation trajectory.

**Do not simply interpolate latitude/longitude linearly.** Use an appropriate projection/geodesic method such as PyProj/GeoPandas where appropriate.

Handle:

* irregular photo intervals
* timezone differences
* duplicate/burst timestamps
* very long gaps between photos
* unrealistic GPS jumps

Use a configurable **time-compression model** so long real-world gaps do not make the video excessively long, while short intervals do not cause the vehicle to teleport or stall.

The animation should produce positions such as:

```python
(timestamp, latitude, longitude, heading)
```

#### 3. Rendering performance

Design the renderer specifically for **thousands of 1080p frames**.

Do **not** regenerate the map background or download map tiles for every frame.

Instead:

* cache/download map tiles once
* pre-render static map backgrounds where possible
* cache static layers
* only redraw dynamic elements each frame
* avoid unnecessary image conversions/copies
* stream frames directly to FFmpeg/PyAV where practical
* use hardware-accelerated H.264 encoding when available, with a software fallback

Prioritize a practical architecture that can actually render a long trip in a reasonable amount of time.

#### 4. Vehicle

Render a configurable transparent car/train icon that:

* follows the interpolated route
* rotates according to heading
* moves smoothly
* leaves an optional traveled-path trail

#### 5. Photo pop-ups

When the vehicle reaches a photo's animation timestamp/location, display the actual photo for a configurable duration.

Handle burst photos intelligently:

* group photos taken very close together in space/time
* avoid dozens of overlapping pop-ups
* avoid covering the vehicle
* use a deterministic layout
* avoid causing camera/viewport jitter

A reasonable default is to show a **small photo cluster or sequential thumbnails** rather than displaying every burst photo simultaneously.

#### 6. Map/camera

Use a renderer appropriate for offline/frame-based video generation, such as:

* Matplotlib + Contextily/GeoPandas
* another proven raster/frame renderer
* existing functionality from `google-timeline-visualizer`

**Do not spend time evaluating Folium, Plotly, or other primarily interactive web-map frameworks.**

Support a smooth camera that can:

* follow the vehicle
* maintain sensible zoom
* avoid jitter
* optionally zoom out for major route transitions

#### 7. Output

Generate:

```text
1920×1080
30 FPS
MP4 / H.264
```

Make FPS, resolution, video duration scaling, popup duration, vehicle, and camera behavior configurable.

---

## Implementation approach

**Do not generate a huge production-ready project all at once.**

Instead, implement this incrementally:

### Phase 1 — Working prototype

Build the smallest complete pipeline:

```text
Photos
→ metadata
→ route
→ interpolation
→ cached/static map
→ moving vehicle
→ MP4
```

Use synthetic/test data if necessary.

### Phase 2 — Photo overlays

Add:

```text
photo timing
→ spatial/temporal clustering
→ popup layout
→ animated appearance/disappearance
```

### Phase 3 — Optimization

Profile the renderer and optimize the actual bottlenecks:

* map rendering
* image processing
* frame generation
* encoding

Do not prematurely optimize components that are not bottlenecks.

---

## What I want from you first

Before writing extensive code, give me:

1. **A short assessment of the currently available code** and what should be reused.
2. **The recommended technology stack**, limited to technologies actually needed.
3. **A concise architecture/data-flow diagram.**
4. **The proposed interpolation/time-scaling strategy.**
5. **The proposed caching/rendering strategy.**
6. **The proposed photo-clustering/layout strategy.**
7. Then implement **Phase 1 only**, with the minimum code necessary to produce a real MP4.

Keep the implementation modular enough to add Phases 2–3 later, but **do not generate unnecessary boilerplate, CLI frameworks, or a large test suite yet**.

Use real, runnable Python code rather than pseudocode where implementation is required.

## Reusable Code

### 1. Existing GitHub Repositories & Specific Projects

* **`mahlernim/google-timeline-visualizer`**: Android/Kotlin project that parses location history (`Timeline.json`), fetches CARTO map tiles, animates vehicle/camera trajectories, and compiles MP4 videos on-device. While written in Kotlin, its tile pre-fetching logic and camera path smoothing formulas serve as a great reference for your Python adaptation.
* **`trackanimation` (PyPI / GitHub)**: A dedicated Python library for creating animated track videos and GIFs from GPS points (`.gpx` or raw coordinate streams). It handles route drawing and frame-by-frame rendering with Matplotlib.
* **`Sbozzolo/motionpicture`**: A Python frame-orchestration library that simplifies parallel frame generation and stitches frames directly into MP4 using FFmpeg via a clean CLI interface.
* **`ptarroso/MapAnim`**: A script using Matplotlib and GDAL to animate raster maps and geospatial overlays over time.

---

### 2. Core Python Libraries by Category

#### EXIF & Metadata Extraction

* **`Pillow` (PIL)**: Python's standard imaging library. Its built-in `_getexif()` function extracts EXIF tags (including `DateTimeOriginal` and raw GPS tags `34853`).
* **`exifread` or `piexif**`: Lightweight libraries specifically built to parse EXIF raw bytes cleanly, convert DMS (Degrees, Minutes, Seconds) to decimal degrees, and safely handle missing tags.

#### Geospatial Interpolation & Route Logic

* **`PyProj`**: Handles map projection conversions (e.g., converting latitude/longitude coordinates into metric projected units like Web Mercator `EPSG:3857` so vehicle movement speed and distances remain accurate).
* **`Shapely`**: Provides geometric operations like path simplification, point-along-line calculation (`line.interpolate()`), and angle/heading calculation for vehicle rotation.
* **`GeoPandas`**: Extends Pandas to handle spatial data frames, making timeline sorting, trajectory creation, and spatial filtering straightforward.
* **`geopy`**: Provides `geodesic` distance functions for computing exact real-world distances between consecutive photos across long distances.
* **`MovingPandas`**: Built on GeoPandas, specifically designed for handling trajectory movement data, spatial smoothing, and trip segmentation.

#### Map Tile & Base Layer Rendering

* **`Contextily`**: Automatically retrieves OpenStreetMap, CARTO, or Stamen map tile rasters for spatial bounds and renders them as Matplotlib backgrounds, with automatic tile caching.
* **`Cartopy`**: High-performance geospatial visualization library for Matplotlib with native support for diverse projections and map overlays.

#### Frame Compositing & Video Export

* **`imageio` / `imageio-ffmpeg**`: High-performance Python wrapper around the FFmpeg binary that streams rendered Numpy/Matplotlib image arrays straight into an H.264 MP4 container without needing to save individual frame files to disk.
* **`MoviePy`**: High-level video editing library useful for adding audio tracks, title cards, fade transitions, and photo pop-up overlays.

---

### Recommended Integration Architecture

```
Photo Folder 
   │
   ├─► Pillow / ExifRead (Extract EXIF GPS & Timestamps)
   │
   ├─► GeoPandas + PyProj (Clean, sort, convert coordinates to Web Mercator)
   │
   ├─► Shapely + GeoPy (Interpolate smooth frames & calculate vehicle rotation)
   │
   ├─► Matplotlib + Contextily (Draw static map tiles + route trail)
   │
   ├─► Pillow / OpenCV (Composite moving vehicle icon & photo popups)
   │
   └─► imageio-ffmpeg (Stream frames direct to MP4)

```