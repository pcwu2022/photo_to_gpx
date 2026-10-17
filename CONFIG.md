# config.json reference

Each project stores its settings in `projects/<name>/config.json`. Edit that file, validate it, and render with the same commands each time:

```bash
python -m photo_to_gpx check projects/test1
python -m photo_to_gpx run projects/test1
```

This reference covers all 50 supported fields. Defaults below match the tuned `projects/test4/config.json` settings and are used by `init`. Existing projects keep their explicit overrides; `examples/demo` keeps its shorter preview settings.

## JSON and path rules

- The file must contain one JSON object. Comments and trailing commas are invalid.
- Omitted fields use the defaults below. Unknown fields are errors.
- Use JSON booleans (`true`, `false`), numbers (`30`, `0.5`), and strings (`"fly"`). Do not quote numbers or booleans.
- All numbers must be finite. Fields described as integers reject fractional values.
- Only `photos`, `gpx`, `duration`, `vehicles`, `icon`, and `music` accept `null`. It means no source/file or automatic duration; it does not restore a field's default.
- Relative paths resolve from the directory containing `config.json`, regardless of your working directory. Absolute input paths are also supported.
- `output` and `cache` must resolve inside the project folder. Custom artwork paths within `vehicles.json` resolve from that file's directory.

For example, `"photos": "photos"` in `projects/test1/config.json` reads `projects/test1/photos/`.

## Input selection

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `photos` | `"photos"` | Existing directory path or `null` | Recursively reads supported images from this directory. Use `"photos"` for the standard layout. `"."` reads the project directory itself, including images in its subdirectories. |
| `gpx` | `null` | Existing GPX file path or `null` | Uses timestamped GPX points instead of photos. Set `photos` to `null` when selecting this input. |
| `demo` | `false` | Boolean | Uses a built-in synthetic route without real photos. Set both `photos` and `gpx` to `null`. |
| `timezone` | `"America/New_York"` | Valid IANA timezone name | Fallback capture timezone, such as `"America/New_York"`, `"Asia/Taipei"`, or `"UTC"`. Also supplies the local timezone for GPX sleep adjustment. |

Exactly one source must be selected. Merely setting `gpx` or `demo` does not disable the default `photos` source.

For photos, an explicit EXIF UTC offset takes priority over `timezone`. If no offset is present, consistent GPS UTC metadata can recover the offset; otherwise the configured timezone is used. A single fallback timezone cannot infer a trip across multiple timezones. Photos need usable capture timestamps and GPS coordinates. Invalid photos are reported as skipped; the route requires at least two accepted photo locations with distinct timestamps.

Photo markers require actual image files. GPX and synthetic inputs normally show the route and vehicle without photo thumbnails.

## Output and encoding

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `output` | `"output/travel.mp4"` | Path ending in `.mp4`, inside the project | Destination video. The containing directory is created automatically. Reports use the same name with `.report.json` and, for photo inputs, `.photo-index.json` replacing `.mp4`. |
| `width` | `1920` | Even integer, at least `320` | Video width in pixels. |
| `height` | `1080` | Even integer, at least `240` | Video height in pixels. |
| `fps` | `30` | Integer from `1` to `120` | Output frames per second. Higher values produce more frames and finer timing, with more rendering work. |
| `encoder` | `"auto"` | String naming an FFmpeg encoder, or `"auto"` | H.264 encoding selection. Automatic selection probes `h264_nvenc`, `h264_qsv`, `h264_videotoolbox`, then `libx264`. An unavailable explicit encoder also falls back to `libx264`. Use `"libx264"` to request software encoding. |

Repeated runs replace the generated destinations. A failed video encode preserves the previous video. The report and photo index are separate files; the photo index can be updated before encoding finishes.

## Journey timing

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `duration` | `null` | Positive number of seconds or `null` | Baseline movement duration before speed controls, pauses, photo spacing, and the overview. With `timing: "time"`, `null` means 90 seconds. With `timing: "compressed"`, `null` uses the sum of the computed segment durations. |
| `timing` | `"time"` | `"time"`, `"compressed"` | `"time"` allocates movement time in proportion to sleep-adjusted real elapsed intervals. `"compressed"` applies the bounded compression formula below to each interval. |
| `compression` | `0.5` | Number greater than `0` and at most `1` | Exponent for compressed timing. Smaller values reduce differences between long and short intervals. Ignored for proportional timing, but must still be valid. |
| `time_scale` | `300` | Positive number | Divisor for real elapsed seconds before compressed timing. Larger values shorten the computed segments before clamping. With proportional timing, the total baseline duration determines the scale instead. |
| `min_segment` | `0.8` | Positive number of seconds | Lower bound on each compressed segment before an explicit `duration` rescale. Ignored for proportional timing, but must still be valid. |
| `max_segment` | `6` | Number of seconds at least `min_segment` | Upper bound on each compressed segment before an explicit `duration` rescale. Ignored for proportional timing, but must still be valid. |

Compressed timing uses:

```text
segment_seconds = clamp(
    (sleep_adjusted_elapsed_seconds / time_scale) ** compression,
    min_segment,
    max_segment
)
```

An explicit `duration` rescales the baseline segments. In compressed mode, rendering rejects an explicit duration that makes a baseline segment shorter than 0.2 seconds. Speed controls can subsequently change movement durations in either mode.

`duration` is therefore not the final MP4 runtime. Final runtime includes retimed motion, photo animations and arrival pauses, any additional waits needed to space photos apart, optional opening titles, and the final overview, rounded to frames. With `music_timing: "music"`, this complete sequence is then scaled to the soundtrack. The report records the actual runtime and timing adjustments.

## Music

Put soundtracks in a `music/` folder beside `photos/`, `config.json`, `vehicles.json`, and `output/`. `init` creates the music folder. Select one file explicitly, for example `"music": "music/song.mp3"`; other files in the folder are ignored.

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `music` | `null` | Existing audio file path or `null` | Soundtrack file. MP3, WAV, M4A, FLAC, OGG, and other audio formats supported by the bundled FFmpeg can be used. The first audio stream is selected. `null` produces a silent video. |
| `music_timing` | `"video"` | `"video"`, `"music"` | `"video"` preserves the native video duration, loops shorter music to fill it, and trims longer music at the end. `"music"` preserves the song's playback speed and uniformly stretches or compresses the entire video to match its duration, within video-frame precision; requires `music`. |

Music starts at the beginning of the video, including an opening title when present. It is encoded as AAC in the MP4; the original audio file is unchanged. In `"music"` mode, title fades, camera transitions, photo pops, movement, pauses, and the ending all scale together. FPS remains unchanged. Speed limits and configured wait durations apply to the native sequence before soundtrack scaling; after scaling they may be faster or slower. Very short tracks can leave fewer frames for individual events.

Reports include `music_seconds`, `music_timing`, `music_looped`, `native_seconds`, and `video_time_scale`. Event arrivals and holds use the output-video clock. `check` decodes the selected audio to validate it and measure its duration, without encoding a video.

## Titles and date timeline

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `title` | `""` | String | Opening and closing title. An empty or whitespace-only string disables both title cards and the opening overview. Text is rendered in uppercase using bundled Inter, with bold title text, a smaller wide-tracked subtitle, and a soft black shadow. Long titles wrap and shrink to fit. |
| `subtitle` | `""` | String | Optional text below the title. Rendered only when `title` is nonempty. |
| `show_timeline` | `true` | Boolean | Shows a subtle bottom date timeline above the map attribution, with a moving train and current date/time. `false` hides it. The previous top timestamp banner is always removed. |

The opening title appears over the complete route's map bounds for `overview_hold` seconds, smoothly fades out, then the camera zooms to the departure over `overview_seconds` seconds. The journey starts after that transition. At the end, the existing overview transition finishes, then the same title fades in and out over the final hold, whose duration is `max(overview_hold, popup_seconds)`. Title fades use smooth easing, with up to 0.8 seconds for each fade, shortened for brief holds. With music fitting, all these durations scale with the complete video.

The timeline uses the actual accepted capture timestamps and the configured `timezone`, rather than animation elapsed time. It stays at the same date during an arrival pause and reaches the final date in the ending overview. It is hidden during the opening title and camera transition, and has a small gap above the attribution footer. Tick selection follows the complete capture range: under one day, every two hours; from one day to less than one calendar month, every local day; otherwise every month. Labels are thinned when needed to avoid overlap, while the ticks remain. Local daily ticks account for daylight-saving offset changes.

## Overnight adjustment and route validation

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `sleep_adjustment` | `true` | Boolean | Subtracts seven hours once from an eligible photo gap before allocating baseline movement time. See the conditions below. |
| `sleep_distance_km` | `1000` | Number at least `0`, in kilometres | A gap's WGS84 distance must be strictly greater than this threshold to qualify for sleep removal. `0` removes the distance threshold for nonzero-distance gaps; it does not disable the adjustment. |
| `max_speed_kmh` | `1200` | Positive number, in kilometres per real hour | GPS plausibility limit. A photo requiring a faster trip from the previous accepted anchor is skipped. This checks real capture timestamps, independently of video playback speed. |
| `gap_hours` | `6` | Positive number of real hours | Reports a gap longer than this threshold as an inferred transfer. This warning does not itself reject a photo or insert a pause. |
| `turn_radius_m` | `1000` | Number at least `0`, in metres | Controls rounding of local route corners. Curves pass through the photo anchors, and their control lengths are also bounded by neighbouring segment lengths. `0` disables local curves. Segments at or above `flight_km` use geodesics. |

Sleep adjustment requires all three conditions: at least seven real hours between captures, a distance strictly above `sleep_distance_km`, and crossing 2 AM in the departure photo's local timezone. It subtracts seven hours once per qualifying gap and retains at least one adjusted second. It does not change original timestamps or the GPS plausibility check. Set `sleep_adjustment` to `false` to disable it.

## Camera

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `camera` | `"fly"` | `"fly"`, `"follow"`, `"fixed"` | `"fly"` follows travel with changing zoom. `"follow"` tracks the vehicle at a fixed journey zoom. `"fixed"` frames the entire route. All modes include the separate final overview. |
| `camera_seconds` | `0.6` | Positive number of seconds | Camera smoothing/transition time scale. Larger values soften following movements. Its effect depends on the camera and speed-control mode; it is not a fixed pause added to each leg. |
| `local_span_km` | `4` | Positive number, in projected kilometres | Local viewport height for flying/following views, with width derived from the aspect ratio. Larger values show a wider area. These are map-projection units, so the ground distance varies with latitude. Speed-limit planning may widen the view further. |
| `flight_km` | `150` | Positive number of kilometres | Long-transfer threshold. It selects long versus short arrival pauses and the cutoff for local route curves. It also controls flight-camera transfer classification when all speed controls are disabled. |
| `city_km` | `5` | Positive number of kilometres | Classifies short city movements. The flying camera stays still while these movements fit safely in the viewport, then pans if needed. When any speed control is active, camera transfer planning uses the city threshold to pull back for longer trips. |

The speed controller is active by default because `min_speed_mps` is nonzero. Changing only `flight_km` therefore changes pauses and curve selection, but may not change the camera's transfer classification.

With photo markers enabled, camera planning reserves room for their full configured diameter, shadow, and timestamp label, plus the bottom timeline and attribution. The camera therefore begins panning before a newly arriving photo would be clipped, while keeping short movements locked when they fit. Increasing `photo_size` also increases this automatic margin; no extra setting is needed. The overview uses the same safe area.

## Animation speed controls

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `min_speed_mps` | `2000` | Number at least `0`, in ground metres per animation second | Shortens moving intervals that would play below this speed. This is accelerated playback, not the real travel speed. `0` disables the floor. |
| `max_screen_speed_px` | `0` | Number at least `0`, in pixels per animation second | Caps rendered movement using the planned camera and zoom. A nonzero ceiling can lengthen movement; `0` disables it. |
| `max_zoom_rate` | `0` | Number at least `0`, in zoom octaves per animation second | Caps camera zoom changes by retiming travel. `1` permits one doubling or halving of scale per second. `0` disables this ceiling. |

These settings affect movement, independently of arrival waits and the overview. If a floor and ceiling conflict, the screen/camera ceiling wins and the report records the conflict. To preserve the baseline timing without speed retiming, set all three to `0`.

## Photos, pauses, and overview

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `show_photos` | `true` | Boolean | Displays persistent circular photo thumbnails at their geographic positions. Also enables extra waits needed to keep photo appearances at least 0.5 seconds apart, rounded up to whole frames. `false` disables these markers, photo animations, and extra photo-spacing waits. |
| `photo_size` | `0.15` | Number greater than `0` and at most `1` | Photo-circle diameter as a fraction of viewport width at the closest planned zoom. `0.15` means 15% of the viewport width; `0.075` halves the default diameter; `0.3` doubles it. The resulting geographic diameter stays fixed, so photos shrink with the map when zooming out. Has no visible effect when `show_photos` is `false`. |
| `photo_animation_seconds` | `0.4` | Number at least `0`, in seconds | Photo pop-in duration, with an eased small-to-full-size transition anchored at the photo's geographic center. Added to the arrival pause for each real photo, including the first photo and same-timestamp bursts. `0` makes photos appear immediately with no fade or scale animation. |
| `trail` | `true` | Boolean | Draws the travelled route behind the vehicle. Future route segments remain hidden. |
| `line_color` | `"#e84553"` | Hex string | Route line color in `#RRGGBB` format, e.g. `"#00A8FF"`. |
| `long_pause` | `0.4` | Number at least `0`, in seconds | Arrival hold after a segment whose distance is at least `flight_km`. `0` disables these holds. |
| `short_pause` | `0.2` | Number at least `0`, in seconds | Arrival hold after a segment shorter than `flight_km`. `0` disables these holds. |
| `overview_seconds` | `3` | Positive number of seconds | Final camera transition to a view of the entire route and all accepted photo markers. Also the opening zoom-in duration when a title is present. |
| `overview_hold` | `5` | Positive number of seconds | Requested hold after the final overview transition, and the opening title-card duration when a title is present. The effective final hold is the larger of this value and `popup_seconds`. |
| `popup_seconds` | `4` | Positive number of seconds | Legacy-named minimum final overview hold. It does not make photo markers expire. |

Setting both arrival pauses to zero does not disable the extra waits used for photo spacing. Those remain while `show_photos` is true. Overview settings and `popup_seconds` currently require positive values; zero cannot disable the final overview.

For a single short-leg arrival, the base hold is `photo_animation_seconds + short_pause`; for a long leg it is `photo_animation_seconds + long_pause`. Animation holds round up to whole source frames so the pop can finish before departure. Same-timestamp photos receive individual animation/hold slots. Additional waits can still be added to satisfy the minimum photo spacing. GPX and synthetic route points without photo files do not receive animation holds. Music fitting scales the resulting waits with the whole video.

## Vehicles and artwork

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `vehicles` | `"vehicles.json"` | Existing JSON file path or `null` | Loads the default icon, custom artwork mappings, and raw-photo-index icon ranges. `null` skips the rules file. |
| `vehicle` | `"car"` | `"car"`, `"train"`, `"bullet_train"`, `"subway"`, `"plane"`, `"boat"`, `"bus"` | Fallback vehicle when the rules file does not specify `default_icon`, or when `vehicles` is `null`. A rules file's `default_icon` overrides this setting. |
| `icon` | `null` | Existing SVG or raster image path, or `null` | Optional replacement artwork for the effective default vehicle. Artwork should face north/up and have a transparent background. A named custom artwork mapping in `vehicles.json` takes precedence over this replacement. |

Vehicle ranges belong in `vehicles.json`, rather than `config.json`. For example:

```json
{
  "default_icon": "car",
  "icons": {"ferry": "icons/ferry.png"},
  "ranges": [
    {"start": 3, "end": 5, "icon": "train"},
    {"start": 6, "end": 8, "icon": "ferry"}
  ]
}
```

Indices are 1-based, inclusive, and assigned before metadata filtering or chronological sorting. They follow case-sensitive relative filenames in natural numeric order, so `2.jpg` precedes `10.jpg`. Photos with unusable metadata still occupy indices. Later overlapping rules win. Each travel leg uses its destination photo's selected vehicle. Inspect `output/travel.photo-index.json` when assigning ranges.

## Map tiles and cache

| Field | Default | Allowed values | Meaning |
| --- | --- | --- | --- |
| `map_provider` | `"esri"` | `"esri"`, `"carto"` | Geographic raster basemap provider. CARTO uncached tile requests require the `CARTO_BASEMAP_API_KEY` environment variable. Keep credentials out of `config.json`. |
| `cache` | `".cache/photo-video"` | Directory path inside the project | Persistent map-tile cache. Provider caches are separated automatically. Reuse it across runs to avoid downloading the same tiles again. |
| `offline` | `false` | Boolean | With geographic maps, `true` permits only cached tiles and fails for missing/corrupt tiles. It does not switch to a schematic map. |
| `plain_map` | `false` | Boolean | Uses a schematic background without geographic tiles or map downloads. Useful for quick offline tests. Overrides geographic basemap rendering. |
| `max_tiles` | `1200` | Integer from `1` to `5000` | Tile budget for camera planning. The renderer lowers map zoom when necessary to fit this budget, reducing detail. |

## Example selections

These snippets are settings to merge into a project's existing config. They are not additional named presets or CLI flags.

### Titled trip fitted to a soundtrack

```json
{
  "music": "music/song.mp3",
  "music_timing": "music",
  "title": "NEW YORK",
  "subtitle": "2026 FALL",
  "overview_hold": 4,
  "photo_animation_seconds": 0.4,
  "show_timeline": true
}
```

Use `"music_timing": "video"` to preserve video timing and loop the music instead. Set `music` to `null` and `music_timing` to `"video"` to return to a silent video.

### Faster local preview

Reduce resolution and frame rate and skip map downloads:

```json
{
  "width": 640,
  "height": 360,
  "fps": 10,
  "duration": 10,
  "plain_map": true,
  "encoder": "libx264"
}
```

The resulting video can differ from ten seconds because the normal speed controls, waits, and overview still apply.

### Proportional timing without speed limits or sleep removal

```json
{
  "timing": "time",
  "duration": 90,
  "sleep_adjustment": false,
  "min_speed_mps": 0,
  "max_screen_speed_px": 0,
  "max_zoom_rate": 0
}
```

This keeps the movement baseline proportional to original elapsed time. Pauses and the overview remain separate.

### GPX input

```json
{
  "photos": null,
  "gpx": "trip.gpx",
  "demo": false
}
```

### Synthetic route without map downloads

```json
{
  "photos": null,
  "gpx": null,
  "demo": true,
  "plain_map": true
}
```

For a complete short demo configuration, see [examples/demo/config.json](examples/demo/config.json).

## Checking changes

`check` validates the JSON settings, source files, vehicle rules, photo metadata, and route construction. For a valid photo route it writes the photo index without downloading maps or encoding a video. Skipped-photo messages do not necessarily mean the check failed: enough valid points can remain to render a route.

`run` additionally plans camera/speed timing, prepares tiles, decodes artwork, probes the encoder, and renders. Errors that depend on these rendering steps can still occur after a successful `check`, including missing offline tiles, unusable artwork, or the compressed-duration minimum described above.

See [README.md](README.md) for project setup and the renderer's implementation details.
